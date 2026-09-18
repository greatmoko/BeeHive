"""运行消息、附件、多模型错误与审批中断辅助函数。"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

from wokbee.core.credential_store import redact_obj, redact_text
from wokbee.core.timeline_format import format_tool_call_for_timeline, format_tool_callback_for_timeline
from wokbee.engine.approval_policy import risk_label_for_tool
from wokbee.engine.ask_user import is_ask_user_interrupt, normalize_ask_user_value
from wokbee.engine.cache_prefix import CacheHitTracker
from wokbee.engine.runner_events import EventCallback

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
        from wokbee.engine.ai_errors import (
            AIErrorKind,
            classify_error,
            display_message,
        )

        kind = classify_error(exc)
        if kind is not AIErrorKind.UNKNOWN:
            # 可自动重试类：deepagents / max_retries 已做底层重试，提示稍后重试；
            # 不可重试类：给出定位与修正建议。
            return display_message(kind, text)
    except Exception:
        pass
    return text




def _attachment_content(text: str, attachments: list[dict] | None) -> Any:
    """将上传附件附加到用户消息；图片使用 OpenAI image_url，多媒体文件给出 uploads 路径。"""
    attachments = attachments or []
    images: list[dict] = []
    files: list[dict] = []
    for item in attachments:
        if not isinstance(item, dict):
            continue
        path = Path(str(item.get("path") or ""))
        if not path.exists() or not path.is_file():
            continue
        if str(item.get("kind") or "") == "image":
            images.append(item)
        else:
            files.append(item)

    file_note = ""
    if files:
        names = []
        for item in files:
            path = Path(str(item.get("path") or ""))
            names.append(f"uploads/{path.name}")
        file_note = "\n\n[附加文件（已保存到项目 uploads/，可用文件工具读取）]\n" + "\n".join(
            f"- {name}" for name in names
        )

    text = (text or "") + file_note
    if not images:
        return text

    parts: list[dict] = []
    if text:
        parts.append({"type": "text", "text": text})
    for item in images:
        path = Path(str(item.get("path") or ""))
        try:
            raw = path.read_bytes()
            mime = str(item.get("mime") or "")
            if not mime:
                import mimetypes
                mime = mimetypes.guess_type(path.name)[0] or "image/png"
            encoded = base64.b64encode(raw).decode("ascii")
            parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{encoded}"},
            })
        except OSError:
            parts.append({"type": "text", "text": f"[无法读取图片附件：{path.name}]"})
    return parts or text


def _message_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                # 兼容 text / output_text 等块
                if block.get("type") in ("text", "output_text", "input_text") or "text" in block:
                    parts.append(str(block.get("text") or ""))
                elif block.get("type") == "reasoning":
                    continue
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts).strip()
    return str(content).strip()


def _reasoning_text(msg: Any) -> str:
    """读取厂商附带的思考/推理文本（如 DeepSeek 的 reasoning_content）。

    标准 ChatOpenAI 解析流式 delta 时丢弃该字段，须先经模型层保留才有值；否则恒空。
    """
    ak = getattr(msg, "additional_kwargs", None)
    if isinstance(msg, dict):
        ak = msg.get("additional_kwargs")
    if not isinstance(ak, dict):
        return ""
    rc = ak.get("reasoning_content") or ""
    if isinstance(rc, list):
        rc = "".join(str(x) for x in rc)
    return str(rc).strip()


def _raw_text(content: Any) -> str:
    """保留空白地抽取消息文本增量（流式 delta 用，勿 strip 以免吞词间空格）。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") in ("text", "output_text", "input_text") or "text" in b:
                    parts.append(str(b.get("text") or ""))
                elif b.get("type") == "reasoning":
                    continue
            elif isinstance(b, str):
                parts.append(b)
        return "".join(parts)
    return str(content)


def _raw_reasoning(chunk: Any) -> str:
    """读取厂商附带的思考 delta（保留空白）。"""
    ak = getattr(chunk, "additional_kwargs", None)
    if isinstance(chunk, dict):
        ak = chunk.get("additional_kwargs")
    if not isinstance(ak, dict):
        return ""
    rc = ak.get("reasoning_content") or ""
    if isinstance(rc, list):
        rc = "".join(str(x) for x in rc)
    return str(rc)


def _stream_delta_parts(chunk: Any) -> tuple[str, str]:
    """把单个流式消息块拆成 (reasoning_delta, text_delta)。"""
    try:
        return _raw_reasoning(chunk), _raw_text(
            getattr(chunk, "content", None) if not isinstance(chunk, dict) else chunk.get("content")
        )
    except Exception:
        return "", ""


def _is_ai_message(msg: Any) -> bool:
    cls = msg.__class__.__name__ if not isinstance(msg, dict) else str(msg.get("type") or "")
    role = getattr(msg, "type", None) or (
        msg.get("role") if isinstance(msg, dict) else None
    ) or cls
    role_s = str(role or "")
    return "AI" in cls or role_s in ("ai", "assistant", "AIMessage")


def _extract_text(messages: list) -> str:
    """取最近一条**非空** AI 正文（跳过空 content / Tool / Human）。"""
    if not messages:
        return ""
    for msg in reversed(list(messages)):
        if not _is_ai_message(msg):
            continue
        content = (
            getattr(msg, "content", None)
            if not isinstance(msg, dict)
            else msg.get("content")
        )
        text = _message_text(content)
        if text:
            return text
        rc = _reasoning_text(msg)
        if rc:
            return rc
    return ""


def _msg_id(msg: Any) -> str:
    mid = getattr(msg, "id", None)
    if mid:
        return str(mid)
    if isinstance(msg, dict) and msg.get("id"):
        return str(msg["id"])
    return ""


def _msg_fingerprint(msg: Any) -> str:
    """稳定去重键：优先消息 id / tool_call_id，否则内容指纹。

    LangGraph stream 后若再遍历 get_state 全量 messages，无 id 时会重复写入时间线。
    """
    mid = _msg_id(msg)
    if mid:
        return f"id:{mid}"

    tcid = getattr(msg, "tool_call_id", None)
    if not tcid and isinstance(msg, dict):
        tcid = msg.get("tool_call_id")
    if tcid:
        return f"toolmsg:{tcid}"

    tc_ids: list[str] = []
    for tc in _tool_calls_of(msg):
        if isinstance(tc, dict):
            tid = str(tc.get("id") or tc.get("tool_call_id") or "").strip()
        else:
            tid = str(getattr(tc, "id", "") or getattr(tc, "tool_call_id", "") or "").strip()
        if tid:
            tc_ids.append(tid)
    if tc_ids:
        return "tc:" + ",".join(tc_ids)

    cls = msg.__class__.__name__ if not isinstance(msg, dict) else str(msg.get("type", "dict"))
    role = getattr(msg, "type", None) or (msg.get("role") if isinstance(msg, dict) else "") or cls
    name = getattr(msg, "name", None) or (msg.get("name") if isinstance(msg, dict) else "") or ""
    text = _message_text(
        getattr(msg, "content", None) if not isinstance(msg, dict) else msg.get("content")
    )[:800]
    # 含 tool_calls 摘要，避免仅文本相同的不同调用被误去重
    if _tool_calls_of(msg):
        bits = []
        for tc in _tool_calls_of(msg)[:6]:
            n, a = _tool_call_parts(tc)
            bits.append(f"{n}:{json.dumps(a, ensure_ascii=False, sort_keys=True)[:120]}")
        text = text + "|" + ";".join(bits)
    raw = f"{role}|{name}|{text}"
    import hashlib

    return "fp:" + hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()[:20]


def _tool_calls_of(msg: Any) -> list:
    tcs = getattr(msg, "tool_calls", None)
    if tcs:
        return list(tcs)
    if isinstance(msg, dict):
        return list(msg.get("tool_calls") or [])
    additional = getattr(msg, "additional_kwargs", None) or {}
    if isinstance(additional, dict):
        return list(additional.get("tool_calls") or [])
    return []


def _tool_call_parts(tc: Any) -> tuple[str, dict]:
    if isinstance(tc, dict):
        name = str(tc.get("name") or tc.get("function", {}).get("name") or "tool")
        args = tc.get("args")
        if args is None and isinstance(tc.get("function"), dict):
            args = tc["function"].get("arguments")
    else:
        name = str(getattr(tc, "name", "tool") or "tool")
        args = getattr(tc, "args", {})
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"raw": args[:500]}
    if not isinstance(args, dict):
        args = {"raw": args}
    return name, args


def _tool_call_id(tc: Any) -> str:
    """取工具调用的 id，用于 call ↔ callback 配对（兼容 dict/对象）。"""
    if isinstance(tc, dict):
        return str(tc.get("id") or tc.get("tool_call_id") or "").strip()
    return str(getattr(tc, "id", "") or getattr(tc, "tool_call_id", "") or "").strip()


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


def _collect_messages_from_update(update: Any) -> list:
    msgs: list = []
    if isinstance(update, dict):
        if "messages" in update:
            raw = update["messages"]
            if isinstance(raw, list):
                msgs.extend(raw)
            else:
                msgs.append(raw)
        else:
            # node_name -> payload
            for v in update.values():
                if isinstance(v, dict) and "messages" in v:
                    raw = v["messages"]
                    if isinstance(raw, list):
                        msgs.extend(raw)
                    else:
                        msgs.append(raw)
                elif isinstance(v, list):
                    msgs.extend(v)
    return msgs


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


def _pending_from_state(agent, config: dict) -> list[dict]:
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
                    "risk": risk_label_for_tool(str(name)),
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
