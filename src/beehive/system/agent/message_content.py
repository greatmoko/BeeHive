"""按职责组织的 message_content。"""
from __future__ import annotations
import base64
import json
from pathlib import Path
from typing import Any


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
