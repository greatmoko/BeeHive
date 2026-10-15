"""任务最终校验、保存、调度注册及结果通知。"""
from __future__ import annotations
import logging
from beehive.features.autobee.engine.cron_description import describe_cron
from beehive.features.autobee.engine.scheduler import get_global_scheduler
from beehive.features.autobee.engine.task_draft import _valid_cron

logger = logging.getLogger("wokbee")

def register_task(d, store, emit):
    # ③ 最终校验：仍缺时间则放弃。
    if not _valid_cron(d.schedule):
        return (
            "无法确定定时任务的执行时间，未创建。"
            "请明确说明执行时间（如：每天下午 6 点半），再让我重试。"
        )
    if not d.name:
        d.name = (d.description or "新的定时任务")[:15]

    task = store.create(
        name=d.name,
        description=d.description,
        task_type=d.task_type,
        schedule=d.schedule,
        cron_text=d.cron_text,
        content=d.content,
        use_ai=d.use_ai,
        code=d.code,
        script_lang=d.script_lang,
        project_id=d.project_id,
        push_wecom=d.push_wecom,
        webhook_url=d.webhook_url,
    )
    registration_error = False
    scheduler = get_global_scheduler()
    if scheduler is not None:
        try:
            scheduler.add_or_update(task)
        except Exception:  # 第三方调度器边界：任务已持久化，需向调用者显式报告注册失败。
            logger.exception("AutoBee 定时任务注册失败：%s", task.id)
            registration_error = True
    if emit:
        try:
            emit(
                "info",
                f"已创建 AutoBee 定时任务：{task.name}（{describe_cron(task.schedule) or task.schedule}）",
                {"autobee_task_id": task.id},
            )
        except Exception:
            pass
    result = d.summary(task.id)
    if registration_error:
        result += "\n警告：任务已保存，但未能注册到运行中的调度器。请在 AutoBee 中检查任务状态或重启应用。"
    return result
