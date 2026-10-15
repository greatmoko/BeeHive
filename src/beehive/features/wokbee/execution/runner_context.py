"""会话上下文首轮注入与 checkpoint 判重。"""
from __future__ import annotations
from typing import Any
from beehive.features.wokbee.execution.prompt import compose_user_with_context
from beehive.system.agent.message_content import _message_text

_CONTEXT_SENTINEL = "【会话上下文】"

def _with_session_context(self, user_message: Any) -> Any:
    context = (self._session_context_block or "").strip()
    if isinstance(user_message, list):
        if not context:
            return user_message
        return [{"type": "text", "text": context}, *user_message]
    return compose_user_with_context(str(user_message or ""), context)

def _context_already_in_state(self, agent, config: dict) -> bool:
    """读 graph 已持久化消息：历史首条 user 是否已注入【会话上下文】。

    run()/run_chat() 每次都会新建 AgentRunner，本 runner 的 _context_injected
    无法跨轮生效，因此落到 checkpointer 的持久化消息上判重：只要首条 user 的消息
    以固定哨兵头开头，就说明本会话已注入过——即使换成新 runner 也成立。
    这保证第 2+ 轮的 user 只含纯问题，命中段最小化。
    """
    try:
        state = agent.get_state(config)
    except Exception:
        return False
    values = getattr(state, "values", None) or {}
    messages = values.get("messages") if isinstance(values, dict) else None
    if not messages:
        return False
    for m in messages:
        role = str(
            getattr(m, "type", None)
            or (m.get("role") if isinstance(m, dict) else "")
            or ""
        )
        if role not in ("user", "human", "HumanMessage"):
            continue
        content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
        if _message_text(content).startswith(_CONTEXT_SENTINEL):
            return True
    return False

def _inject_session_context_once(
    self, payload: Any, *, agent=None, config: dict | None = None
) -> Any:
    """仅首条用户消息注入【会话上下文】，保持后续轮次 append-only 前缀稳定。

    去重依据二选一（满足即跳过）：
    1. 本 runner 已注入过（同轮内多阶段/续跑去重）；
    2. graph 持久化消息已含固定哨兵头（跨新 runner 去重）。
    """
    if self._context_injected or not isinstance(payload, dict):
        return payload
    msgs = payload.get("messages")
    if not isinstance(msgs, list) or not msgs:
        return payload
    if agent is not None and config is not None and self._context_already_in_state(agent, config):
        self._context_injected = True
        return payload
    out_msgs = []
    injected = False
    for m in msgs:
        if (
            not injected
            and isinstance(m, dict)
            and (m.get("role") or "") == "user"
        ):
            content = m.get("content") or ""
            out_msgs.append(
                {**m, "content": self._with_session_context(content)}
            )
            injected = True
        else:
            out_msgs.append(m)
    if injected:
        self._context_injected = True
        return {**payload, "messages": out_msgs}
    return payload
