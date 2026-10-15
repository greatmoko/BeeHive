"""任务结果通知。"""
from __future__ import annotations
import logging
from beehive.features.autobee.core.models import ScheduledTask
from beehive.features.autobee.engine.wecom import push_wecom

logger = logging.getLogger("autobee")

def maybe_notify_wechat(executor, task: ScheduledTask, result: dict) -> None:
    if not task.notify_wechat or executor._notification_sender is None:
        return
    status = "执行完成" if result.get("ok") else "执行失败"
    detail = (result.get("message") or result.get("error") or "（无输出）").strip()
    try:
        ok, error = executor._notification_sender(f"AutoBee 任务「{task.name}」{status}\n{detail[:3500]}")
        if not ok:
            logger.warning("任务 %s 微信完成通知失败：%s", task.id, error)
    except Exception:
        logger.exception("任务 %s 微信完成通知异常", task.id)

def maybe_push(executor, task: ScheduledTask, result: dict) -> None:
    """任务结果（成功或失败）按需推送到企业微信。"""
    if not task.push_wecom:
        return
    webhook = (task.webhook_url or "").strip()
    payload = (result.get("message") or "").strip() or (result.get("error") or "").strip()
    if not webhook:
        suffix = "[推送失败] 未配置企业微信 Webhook"
        result["message"] = " ".join(x for x in (result.get("message"), suffix) if x)
        return
    if not payload:
        payload = "(任务无输出)"
    ok, msg = push_wecom(webhook, payload, msgtype=task.msgtype, mention=task.mention)
    if not ok:
        base = (result.get("error") or result.get("message") or "").strip("；")
        result["error"] = f"{base}；推送失败：{msg}".strip("；") if base else f"推送失败：{msg}"
