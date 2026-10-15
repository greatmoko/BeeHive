"""定时任务执行协调：持有存储、并发锁与回调，按类型分派。"""
from __future__ import annotations
import logging
import threading
from typing import Any, Callable
from beehive.system.providers.provider_store import ProviderStore
from beehive.system.providers.provider_models import ResolvedModel
from beehive.features.wokbee.projects.models import Project
from beehive.features.wokbee.projects.project_store import ProjectStore
from beehive.features.wokbee.settings import WokBeeSettings
from beehive.features.autobee.core.models import ScheduledTask, TaskType
from beehive.features.autobee.core.store import AutoBeeStore
from beehive.features.autobee.engine import task_text
from beehive.features.autobee.engine import task_script
from beehive.features.autobee.engine import task_notifications
from beehive.features.autobee.engine import task_project

logger = logging.getLogger("autobee")

class TaskExecutor:
    """调度线程同步调用；存储和并发锁由协调器持有，不操作 Qt 控件。"""

    def __init__(
        self,
        store: AutoBeeStore | None = None,
        project_store: ProjectStore | None = None,
        provider_store: ProviderStore | None = None,
        settings: WokBeeSettings | None = None,
        notification_sender: Callable[[str], tuple[bool, str]] | None = None,
        event_sink: Callable[[str, str, str, dict], None] | None = None,
    ):
        self.store = store or AutoBeeStore()
        self.project_store = project_store or ProjectStore()
        self.provider_store = provider_store or ProviderStore()
        self.settings = settings or WokBeeSettings()
        self._notification_sender = notification_sender
        self._event_sink = event_sink
        # 按 project_id 加锁，防两个任务并发跑同一项目、共用 checkpointer 互相覆盖
        self._project_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        # 进度回调按 task_id 键控，避免两个并发任务互相覆盖、把 A 的进度写进 B 的 last_message
        self._progress_handlers: dict[str, Callable[[str], None]] = {}
        self._progress_guard = threading.Lock()

    def set_progress_handler(self, task_id: str, handler: Callable[[str], None] | None) -> None:
        with self._progress_guard:
            if handler is None:
                self._progress_handlers.pop(task_id, None)
            else:
                self._progress_handlers[task_id] = handler

    def _emit_progress(self, task_id: str, message: str) -> None:
        with self._progress_guard:
            handler = self._progress_handlers.get(task_id)
        if handler:
            try:
                handler(message)
            except Exception:
                logger.exception("进度回调失败")

    def _project_lock(self, project_id: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._project_locks.get(project_id)
            if lock is None:
                lock = threading.Lock()
                self._project_locks[project_id] = lock
            return lock

    def run(self, task: ScheduledTask) -> dict:
        """执行任务，返回 {ok, message, error}；可选结果推送企业微信。"""
        ttype = task.task_type
        if ttype == TaskType.TEXT:
            result = self._run_text(task)
        elif ttype == TaskType.SCRIPT:
            result = self._run_script(task)
        elif ttype == TaskType.WOKBEE:
            result = self._run_wokbee(task)
        else:
            return {"ok": False, "message": "", "error": f"未知任务类型：{ttype}"}
        self._maybe_push(task, result)
        self._maybe_notify_wechat(task, result)
        return result

    def _resolve_model(self, task: ScheduledTask, for_gen: bool) -> ResolvedModel:
        """解析生成/执行的模型：任务绑定 → 厂商默认 → 列表第一个。"""
        if for_gen:
            pid, mid = task.gen_provider, task.gen_model_id
        else:
            pid, mid = task.exec_provider, task.exec_model_id
        if pid and mid:
            resolved = self.provider_store.resolve(pid, mid)
            if resolved:
                return resolved
        default = self.provider_store.resolve_default()
        if default:
            return default
        first = self.provider_store.first_resolved()
        if not first:
            raise ValueError("没有可用模型，请先在「AI配置 → 厂商设置」中启用模型并填写 Key/Host。")
        return first

    def _run_text(self, task: ScheduledTask):
        return task_text.run_text(self, task)

    def _run_script(self, task: ScheduledTask):
        return task_script.run_script(self, task)

    @staticmethod
    def _script_cmd(lang: str, code: str):
        return task_script.script_cmd(lang, code)

    def _maybe_notify_wechat(self, task: ScheduledTask, result: dict):
        return task_notifications.maybe_notify_wechat(self, task, result)

    def _maybe_push(self, task: ScheduledTask, result: dict):
        return task_notifications.maybe_push(self, task, result)

    def _run_wokbee(self, task: ScheduledTask):
        return task_project.run_wokbee(self, task)

    def _resolve_exec_model(self, task: ScheduledTask, project: Project):
        return task_project.resolve_exec_model(self, task, project)

    def _wire_runner_callbacks(self, runner: AgentRunner, task: ScheduledTask, project: Project):
        return task_project.wire_runner_callbacks(self, runner, task, project)

    def _append_project_event(self, project_id: str, kind: str, content: str, meta: dict):
        return task_project.append_project_event(self, project_id, kind, content, meta)

    def _emit_project_event(self, project_id: str, kind: str, content: str, meta: dict):
        return task_project.emit_project_event(self, project_id, kind, content, meta)

    def _finalize_wokbee_status(self, project_id: str, result: Any):
        return task_project.finalize_wokbee_status(self, project_id, result)
