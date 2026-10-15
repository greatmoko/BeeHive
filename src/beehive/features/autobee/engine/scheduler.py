"""定时任务调度：包装 APScheduler BackgroundScheduler，后台线程运行，不阻塞 UI。"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from PySide6.QtCore import QObject, Signal
from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_MISSED,
)
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from beehive.features.autobee.core.models import JobLog, ScheduledTask, TaskRunStatus, _now
from beehive.features.autobee.core.store import AutoBeeStore
from beehive.features.autobee.engine.executor import TaskExecutor

logger = logging.getLogger("autobee")

# 失联宽限（秒）：电脑睡眠到点后仍能在宽限内补跑，避免默认 1s 静默丢弃
_MISFIRE_GRACE = 3600

# 进程内全局调度器引用：由 ServiceRegistry 启动时注册，供 Agent 能力（autobee_tools）
# 在任意线程创建任务后，立即挂到运行中的调度器上，无需重启应用。
_GLOBAL_SCHEDULER: "SchedulerService | None" = None
_GLOBAL_LOCK = threading.Lock()


def register_global_scheduler(scheduler: "SchedulerService | None") -> None:
    """注册全局调度器引用（应用启动时调用一次）。"""
    global _GLOBAL_SCHEDULER
    with _GLOBAL_LOCK:
        _GLOBAL_SCHEDULER = scheduler


def get_global_scheduler() -> "SchedulerService | None":
    with _GLOBAL_LOCK:
        return _GLOBAL_SCHEDULER


class SchedulerNotifier(QObject):
    """调度器 → UI 的 Qt 信号桥（跨线程自动 QueuedConnection）。"""

    task_started = Signal(str)          # task_id
    task_progress = Signal(str, str)    # task_id, message
    task_finished = Signal(str, str, str)  # task_id, status, message


from beehive.features.autobee.engine.cron_description import describe_cron


from beehive.features.autobee.engine.cron_description import _all_star


from beehive.features.autobee.engine.cron_description import _is_int


class SchedulerService:
    """管理任务注册到 APScheduler，并记录运行日志。"""

    def __init__(self, store: AutoBeeStore | None = None, executor: TaskExecutor | None = None):
        self.store = store or AutoBeeStore()
        self.executor = executor or TaskExecutor(self.store)
        self.notifier = SchedulerNotifier()
        self._started = False
        self._lock = threading.RLock()
        self._running_tasks: set[str] = set()
        self._pool = ThreadPoolExecutor(max_workers=2)
        self._scheduler = BackgroundScheduler(
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": _MISFIRE_GRACE,
            },
        )
        self._scheduler.add_listener(self._on_scheduler_event, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED)

    # ── 生命周期 ───────────────────────────────────────────
    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            for task in self.store.list_tasks():
                self._register(task)
            self._scheduler.start()
            self._started = True

    def shutdown(self, *, wait: bool = False) -> None:
        with self._lock:
            if not self._started:
                return
            try:
                self._scheduler.shutdown(wait=wait)
            except Exception:
                logger.exception("关闭调度器失败")
            self._started = False

    @property
    def running(self) -> bool:
        return self._started

    # ── 任务注册 ───────────────────────────────────────────
    def _register(self, task: ScheduledTask) -> None:
        """按 id 注册/更新 job；禁用任务补 pause。replace_existing 会重建激活，须补 pause。"""
        try:
            trigger = CronTrigger.from_crontab(task.schedule)
        except ValueError as e:
            logger.warning("任务 %s 的 cron 无效：%s", task.id, e)
            return
        self._scheduler.add_job(
            self._run_job, trigger, args=[task.id], id=task.id, replace_existing=True,
        )
        if not task.enabled:
            try:
                self._scheduler.pause_job(task.id)
            except Exception:
                logger.exception("暂停任务 %s 失败", task.id)

    def add_or_update(self, task: ScheduledTask) -> None:
        with self._lock:
            self._register(task)

    def remove(self, task_id: str) -> None:
        with self._lock:
            try:
                self._scheduler.remove_job(task_id)
            except Exception:
                pass

    def pause(self, task_id: str) -> None:
        with self._lock:
            self.store.set_enabled(task_id, False)
            try:
                self._scheduler.pause_job(task_id)
            except Exception:
                logger.exception("停用任务 %s 失败", task_id)

    def resume(self, task_id: str) -> None:
        with self._lock:
            self.store.set_enabled(task_id, True)
            task = self.store.get(task_id)
            try:
                self._scheduler.resume_job(task_id)
            except Exception:
                # job 可能尚未注册或已被重建：按当前任务重新挂上
                if task is not None:
                    self._register(task)
                else:
                    logger.exception("启用任务 %s 失败", task_id)

    def run_now(self, task_id: str) -> bool:
        """立即执行（用自管线程池投递，避开 APScheduler 无公开 submit）。"""
        task = self.store.get(task_id)
        if task is None:
            return False
        with self._lock:
            if task_id in self._running_tasks:
                logger.info("任务 %s 已在运行，忽略重复触发", task_id)
                return False
        self._pool.submit(self._run_job, task_id)
        return True

    def is_task_running(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._running_tasks

    def any_task_running(self) -> bool:
        with self._lock:
            return bool(self._running_tasks)

    # ── 查询 ───────────────────────────────────────────────
    def next_run_time(self, task_id: str) -> str:
        job = self._scheduler.get_job(task_id)
        if job is None:
            return "未调度"
        next_t = job.next_run_time
        if next_t is None:
            return "已暂停"
        return next_t.astimezone().strftime("%Y-%m-%d %H:%M")

    def is_paused(self, task_id: str) -> bool:
        job = self._scheduler.get_job(task_id)
        return job is None or job.next_run_time is None

    # ── 执行与记录 ─────────────────────────────────────────
    def _run_job(self, task_id: str) -> None:
        task = self.store.get(task_id)
        if task is None:
            return
        with self._lock:
            if task_id in self._running_tasks:
                logger.info("任务 %s 已在运行，跳过", task_id)
                return
            self._running_tasks.add(task_id)

        log = JobLog(task_id=task_id, status=TaskRunStatus.RUNNING)
        started_dt = datetime.now()
        task.last_status = TaskRunStatus.RUNNING.value
        task.last_message = "运行中…"
        task.last_run = log.started_at
        # 启动日志单独 try：写盘失败也不应让任务卡在 _running_tasks（B3）
        try:
            self.store.save_task(task)
            self.store.append_log(log)
        except Exception:
            logger.exception("任务 %s 启动日志写入失败", task_id)
        self.notifier.task_started.emit(task_id)

        def _on_progress(message: str) -> None:
            brief = (message or "").strip()
            if not brief:
                return
            if len(brief) > 200:
                brief = brief[:200] + "…"
            task.last_message = brief
            try:
                self.store.save_task(task)
            except Exception:
                logger.exception("任务 %s 进度写盘失败", task_id)
            self.notifier.task_progress.emit(task_id, brief)

        try:
            self.executor.set_progress_handler(task_id, _on_progress)
            result = self.executor.run(task)
            ok = bool(result.get("ok"))
            log.status = TaskRunStatus.SUCCESS if ok else TaskRunStatus.FAILED
            log.summary = str(result.get("message") or "")
            log.error = str(result.get("error") or "")
            task.last_status = log.status.value
            task.last_message = log.summary or log.error or log.status.label
        except Exception as e:
            logger.exception("任务 %s 执行异常", task_id)
            log.status = TaskRunStatus.FAILED
            log.error = str(e)
            task.last_status = TaskRunStatus.FAILED.value
            task.last_message = str(e)
        finally:
            self.executor.set_progress_handler(task_id, None)
            log.finished_at = _now()
            log.duration_s = max(0.0, (datetime.now() - started_dt).total_seconds())
            try:
                self.store.update_log(log)
            except Exception:
                logger.exception("任务 %s 结束日志写盘失败", task_id)
            task.last_run = log.started_at
            try:
                self.store.save_task(task)
            except Exception:
                logger.exception("任务 %s 结束状态写盘失败", task_id)
            # 无论写成功与否，都保证任务从运行集合移除，防止永久卡死（B3）
            with self._lock:
                self._running_tasks.discard(task_id)
            self.notifier.task_finished.emit(
                task_id, task.last_status, task.last_message or "",
            )

    def _on_scheduler_event(self, event) -> None:
        try:
            job_id = getattr(event, "job_id", "")
            if event.code == EVENT_JOB_MISSED:
                log = JobLog(task_id=job_id, status=TaskRunStatus.MISSED, summary="错过执行")
                self.store.append_log(log)
                task = self.store.get(job_id)
                if task:
                    task.last_status = TaskRunStatus.MISSED.value
                    task.last_message = "错过执行（可能当时电脑未开机）"
                    self.store.save_task(task)
            elif event.code == EVENT_JOB_ERROR:
                exc = getattr(event, "exception", None)
                log = JobLog(
                    task_id=job_id, status=TaskRunStatus.FAILED,
                    summary=task_summary(self.store, job_id),
                    error=getattr(exc, "str", None) or str(exc)[:300],
                )
                self.store.append_log(log)
        except Exception:
            logger.exception("写入调度事件日志失败")


from beehive.features.autobee.engine.cron_description import task_summary
