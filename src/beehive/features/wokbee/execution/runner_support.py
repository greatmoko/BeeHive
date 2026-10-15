"""运行消息、附件、多模型错误与审批中断辅助函数。"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

from beehive.system.credentials.credential_store import redact_obj
from beehive.system.credentials.credential_store import redact_text
from beehive.features.wokbee.projects.timeline_format import format_tool_call_for_timeline, format_tool_callback_for_timeline
from beehive.features.wokbee.execution.approval_policy import risk_label_for_tool
from beehive.system.agent.ask_user import is_ask_user_interrupt, normalize_ask_user_value
from beehive.system.agent.cache_stats import CacheHitTracker
from beehive.system.agent.execution_events import EventCallback

logger = logging.getLogger("wokbee")


def _format_engine_error(exc: BaseException) -> str:
    """把厂商网关错误翻成可读说明，避免和本机 MCP 调用失败混在一起。"""
    text = str(exc)
    low = text.lower()

    # 已有明确可读原始信息时优先（网关/MCP 特判），避免被分类覆盖。
    if (
        "do_request_failed" in low
        or "upstream error" in low
        or ("error code: 500" in low and "new_api" in low)
    ):
        return (
            "模型网关返回 500（上游请求失败）。这是厂商把请求转到 DeepSeek 时失败，"
            "发生在模型 HTTP 调用阶段，不是 MCP 工具执行失败。"
            "请稍后重试；若连续出现，可暂时关掉部分 MCP 减少 tools 数量，"
            "或到网关后台用 request id 查日志。"
            f"\n原始信息：{text}"
        )
    if "does not support sync invocation" in low:
        return (
            "MCP 工具缺少同步入口。请确认已更新并重启应用后再试。"
            f"\n原始信息：{text}"
        )

    # 统一分类兜底：命中已知类时给出针对性诊断/建议。
    try:
        from beehive.common.ai_error_kind import AIErrorKind
        from beehive.common.ai_errors import classify_error
        from beehive.common.ai_errors import display_message

        kind = classify_error(exc)
        if kind is not AIErrorKind.UNKNOWN:
            # 可自动重试类：deepagents / max_retries 已做底层重试，提示稍后重试；
            # 不可重试类：给出定位与修正建议。
            return display_message(kind, text)
    except Exception:
        pass
    return text




from beehive.system.agent.message_content import _attachment_content


from beehive.system.agent.message_content import _message_text


from beehive.system.agent.message_content import _reasoning_text


from beehive.system.agent.message_content import _raw_text


from beehive.system.agent.message_content import _raw_reasoning


from beehive.system.agent.message_content import _stream_delta_parts


from beehive.system.agent.message_content import _is_ai_message


from beehive.system.agent.message_content import _extract_text


from beehive.system.agent.message_content import _msg_id


from beehive.system.agent.message_content import _msg_fingerprint


from beehive.system.agent.message_content import _tool_calls_of


from beehive.system.agent.message_content import _tool_call_parts


from beehive.system.agent.message_content import _tool_call_id


def _format_tool_call(tc: Any) -> str:
    name, args = _tool_call_parts(tc)
    args = redact_obj(args)
    args_s = json.dumps(args, ensure_ascii=False)
    if len(args_s) > 400:
        args_s = args_s[:400] + "…"
    return f"{name}({args_s})"


def build_success_path_from_messages(messages: list, *, limit: int = 40) -> str:
    """从本轮消息中的工具调用轨迹提炼「成功实现路径」。"""
    steps: list[str] = []
    for msg in messages or []:
        if len(steps) >= limit:
            break
        cls = msg.__class__.__name__ if not isinstance(msg, dict) else str(msg.get("type", ""))
        role = getattr(msg, "type", None) or (msg.get("role") if isinstance(msg, dict) else "") or cls

        for tc in _tool_calls_of(msg):
            if len(steps) >= limit:
                break
            steps.append(f"{len(steps) + 1}. call: {_format_tool_call(tc)}")

        if "Tool" in cls or role in ("tool", "ToolMessage"):
            name = (
                getattr(msg, "name", None)
                or (msg.get("name") if isinstance(msg, dict) else None)
                or "tool"
            )
            body = _message_text(
                getattr(msg, "content", None) if not isinstance(msg, dict) else msg.get("content")
            )
            body = redact_text(body)
            if str(name) == "get_credential":
                body = "（凭据明文已隐藏）"
            if len(body) > 220:
                body = body[:220] + "…"
            if body:
                steps.append(f"{len(steps) + 1}. callback: {name} — {body}")
            else:
                steps.append(f"{len(steps) + 1}. callback: {name}")

    if not steps:
        return ""
    return "\n".join(steps)


def _emit_message_events(
    emit: EventCallback,
    msg: Any,
    seen: set[str],
    *,
    cache_tracker: CacheHitTracker | None = None,
) -> None:
    """把单条消息转成时间线事件（跳过已发过的 id/指纹）。"""
    key = _msg_fingerprint(msg)
    if key in seen:
        return
    seen.add(key)

    cls = msg.__class__.__name__ if not isinstance(msg, dict) else msg.get("type", "")
    role = getattr(msg, "type", None) or (msg.get("role") if isinstance(msg, dict) else "") or cls

    # ToolMessage
    if "Tool" in cls or role in ("tool", "ToolMessage"):
        name = getattr(msg, "name", None) or (msg.get("name") if isinstance(msg, dict) else "") or "tool"
        body = _message_text(
            getattr(msg, "content", None) if not isinstance(msg, dict) else msg.get("content")
        )
        tcid = getattr(msg, "tool_call_id", None)
        if not tcid and isinstance(msg, dict):
            tcid = msg.get("tool_call_id")
        status = "success"
        if isinstance(msg, dict):
            status = str(msg.get("status") or "success").lower()
        else:
            status = str(getattr(msg, "status", "success") or "success").lower()
        emit(
            "tool",
            format_tool_callback_for_timeline(str(name), body),
            {
                "tool": name,
                "phase": "callback",
                "tool_call_id": str(tcid or ""),
                "status": status,
            },
        )
        return

    # AIMessage / assistant
    if "AI" in cls or role in ("ai", "assistant", "AIMessage"):
        if cache_tracker is not None:
            try:
                cache_tracker.observe_message(msg)
            except Exception:
                logger.exception("cache hit 观测失败")
        tcs = _tool_calls_of(msg)
        text = _message_text(
            getattr(msg, "content", None) if not isinstance(msg, dict) else msg.get("content")
        )
        reasoning = _reasoning_text(msg)
        if reasoning:
            emit("agent", reasoning, {"phase": "reasoning"})
        # AI 正文总是发射：有工具调用时作为「旁白/指挥」先于 call 展示，
        # 无工具调用时作为「AI 回答」→ 顺序: reasoning → 旁白 → call1..N → callback1..N
        if text:
            emit("agent", text, {"phase": "narration" if tcs else "answer"})
        for tc in tcs:
            name, args = _tool_call_parts(tc)
            emit(
                "tool",
                format_tool_call_for_timeline(name, args),
                {
                    "phase": "call",
                    "tool": name,
                    "args": args,
                    "tool_call_id": _tool_call_id(tc),
                },
            )
        return

    # Human / other — 一般不回显用户消息（UI 已有）
    return


from beehive.system.agent.message_content import _collect_messages_from_update


from beehive.features.wokbee.execution.interrupt_state import _iter_interrupt_values


from beehive.features.wokbee.execution.interrupt_state import _first_ask_user_payload


from beehive.features.wokbee.execution.interrupt_state import _pending_from_state


from beehive.features.wokbee.execution.interrupt_state import _has_pending
