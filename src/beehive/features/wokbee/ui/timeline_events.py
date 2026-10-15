"""网页时间线事件归类、工具文本、脱敏序列化和分页常量。"""
from __future__ import annotations
import json
import re
from beehive.features.wokbee.projects.models import ProjectEvent
from beehive.features.wokbee.ui.timeline_text import _redact_display
import json
from beehive.features.wokbee.projects.models import ProjectEvent
from beehive.features.wokbee.ui.timeline_text import _redact_display
INITIAL_RENDER = 50


LOAD_OLDER_BATCH = 50


def _event_ui_role(kind: str) -> str:
    """归类：user | ai | tool | system | error"""
    if kind == "user":
        return "user"
    if kind == "agent":
        return "ai"
    if kind == "tool":
        return "tool"
    if kind == "error":
        return "error"
    return "system"  # info / approval / lesson / …


def _tool_event_display_text(ev: ProjectEvent) -> str:
    """工具气泡正文：优先用结构化 meta 格式化，避免 call 挤成一行。"""
    meta = ev.meta if isinstance(ev.meta, dict) else {}
    phase = str(meta.get("phase") or "").lower()
    tool = str(meta.get("tool") or "").strip()
    content = (ev.content or "").strip()

    # 已是新格式
    if content.startswith("**call:**") or content.startswith("**callback:**"):
        return content

    if phase == "call" and tool:
        args = meta.get("args") if isinstance(meta.get("args"), dict) else {}
        try:
            from beehive.features.wokbee.projects.timeline_format import format_tool_call_for_timeline

            return format_tool_call_for_timeline(tool, args)
        except Exception:
            pass
        # 轻量回退
        lines = [f"**call:** `{tool}`"]
        for k, v in list(args.items())[:8]:
            s = str(v)
            if k in ("content", "body", "text", "command") and len(s) > 200:
                s = s[:200] + f"…（共 {len(str(v))} 字）"
            s = s.replace("\\n", "\n")
            if "\n" in s:
                lines.append(f"- **{k}:**")
                lines.extend(f"  {ln}" for ln in s.splitlines()[:12])
            else:
                lines.append(f"- **{k}:** {s}")
        return "\n".join(lines)

    if phase == "callback" and tool:
        body = content
        for prefix in (f"callback: {tool}", f"callback: {tool}\n"):
            if body.startswith(prefix):
                body = body[len(prefix) :].lstrip("\n")
                break
        if body.startswith("callback:"):
            body = body.split("\n", 1)[-1] if "\n" in body else ""
        try:
            from beehive.features.wokbee.projects.timeline_format import format_tool_callback_for_timeline

            return format_tool_callback_for_timeline(tool, body or content)
        except Exception:
            return f"**callback:** `{tool}`\n\n```\n{(body or content)[:2000]}\n```"

    # 旧版纯文本 call: write_file({...})
    if content.startswith("call: ") or content.startswith("call:"):
        rest = content.split(":", 1)[-1].strip()
        m = re.match(r"^([A-Za-z_][\w]*)\((.*)\)\s*$", rest, re.DOTALL)
        if m:
            name, args_s = m.group(1), m.group(2).strip()
            args: dict = {}
            try:
                val = json.loads(args_s)
                if isinstance(val, dict):
                    args = val
            except json.JSONDecodeError:
                # 截断的 JSON：尽量展示原始多行
                pretty = args_s.replace("\\n", "\n")
                return f"**call:** `{name}`\n\n```\n{pretty[:2500]}\n```"
            try:
                from beehive.features.wokbee.projects.timeline_format import format_tool_call_for_timeline

                return format_tool_call_for_timeline(name, args)
            except Exception:
                return f"**call:** `{name}`\n\n```\n{args_s[:2000]}\n```"

    if content.startswith("callback:"):
        parts = content.split("\n", 1)
        head = parts[0]
        body = parts[1] if len(parts) > 1 else ""
        name = head.split(":", 1)[-1].strip() or "tool"
        return f"**callback:** `{name}`\n\n```\n{body[:2000]}\n```"

    return content
def _event_to_dict(ev: ProjectEvent) -> dict:
    """把事件转成前端可渲染的 dict（含工具行配对信息与脱敏后的展示文本）。"""
    meta = ev.meta if isinstance(ev.meta, dict) else {}
    phase = str(meta.get("phase") or "")
    if ev.kind == "tool":
        body = _tool_event_display_text(ev)
    else:
        body = ev.content or ""
    d: dict = {
        "kind": ev.kind,
        "role": _event_ui_role(ev.kind),
        "phase": phase,
        "time": ev.created_at,
        "content": _redact_display(body),
        "session_end": bool(meta.get("session_end")),
    }
    atts = meta.get("attachments")
    if isinstance(atts, list) and atts:
        d["attachments"] = atts
    if ev.kind == "deliverables":
        d["deliverable"] = True
        d["project_id"] = str(meta.get("project_id") or "")
    if ev.kind == "tool":
        d["tool"] = {
            "id": str(meta.get("tool_call_id") or ""),
            "name": str(meta.get("tool") or "tool"),
            "phase": str(meta.get("phase") or "").lower(),
            "status": str(meta.get("status") or "success").lower(),
        }
    return d
