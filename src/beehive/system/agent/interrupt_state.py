"""通用 graph 中断解析与敏感参数脱敏。"""
from __future__ import annotations
import logging
from beehive.system.credentials.credential_store import redact_obj
from beehive.system.credentials.credential_store import redact_text
from beehive.system.agent.ask_user import is_ask_user_interrupt, normalize_ask_user_value

logger = logging.getLogger("wokbee")

def _iter_interrupt_values(agent, config: dict):
    """遍历 checkpoint 中未处理的 interrupt 值。"""
    try:
        state = agent.get_state(config)
    except Exception as e:
        logger.warning("get_state 失败: %s", e)
        return
    tasks = getattr(state, "tasks", None) or ()
    for task in tasks:
        interrupts = getattr(task, "interrupts", None) or ()
        for intr in interrupts:
            yield getattr(intr, "value", intr)

def _first_ask_user_payload(agent, config: dict) -> dict | None:
    for value in _iter_interrupt_values(agent, config):
        if is_ask_user_interrupt(value):
            return normalize_ask_user_value(value)
        # 兼容：仅含 questions 的载荷
        if isinstance(value, dict) and value.get("questions") and not (
            value.get("action_requests") or value.get("actions")
        ):
            return normalize_ask_user_value({**value, "type": "ask_user"})
    return None

def _pending_from_state(agent, config: dict, *, risk_label) -> list[dict]:
    """从 checkpoint state 解析待审批动作（跳过 ask_user 澄清中断）。"""
    pending: list[dict] = []
    for value in _iter_interrupt_values(agent, config):
        if is_ask_user_interrupt(value):
            continue
        if isinstance(value, dict) and value.get("questions") and not (
            value.get("action_requests") or value.get("actions")
        ):
            continue

        action_requests = None
        if isinstance(value, dict):
            action_requests = value.get("action_requests") or value.get("actions")
        else:
            action_requests = getattr(value, "action_requests", None)

        if not action_requests:
            # 兜底：整包当作一条（非 ask_user）
            pending.append(
                {
                    "name": "tool",
                    "args": {},
                    "description": redact_text(str(value)[:500]),
                    "risk": "操作",
                }
            )
            continue

        for action in action_requests:
            if isinstance(action, dict):
                name = action.get("name") or action.get("tool") or "tool"
                args = action.get("args") or action.get("arguments") or {}
            else:
                name = getattr(action, "name", None) or "tool"
                args = getattr(action, "args", {}) or {}
            pending.append(
                {
                    "name": str(name),
                    "args": args if isinstance(args, dict) else {"raw": str(args)},
                    "description": f"{name}({args})"[:400],
                    "risk": risk_label(str(name)),
                }
            )
    for item in pending:
        item["args"] = redact_obj(item.get("args") or {})
        item["description"] = redact_text(str(item.get("description") or ""))
    return pending

def _has_pending(agent, config: dict) -> bool:
    try:
        state = agent.get_state(config)
        return bool(getattr(state, "next", None))
    except Exception:
        return False
