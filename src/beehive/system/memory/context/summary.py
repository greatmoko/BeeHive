"""按职责组织的 summary。"""
from __future__ import annotations
from beehive.system.prompts import SUMMARY_SYSTEM_PROMPT
from datetime import datetime


def mechanical_summary(messages: list[dict], previous_summary: str = "") -> str:
    """无模型时的保底摘要。"""
    lines: list[str] = []
    if previous_summary.strip():
        lines.append(f"此前摘要：{previous_summary.strip()[:800]}")
    for m in messages:
        role = m.get("role") or "user"
        content = m.get("content") or ""
        if isinstance(content, list):
            parts = [
                str(p.get("text") or "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            content = " ".join(parts)
        text = str(content).strip().replace("\n", " ")
        if not text:
            if m.get("attachments"):
                text = "[附件]"
            else:
                continue
        if len(text) > 120:
            text = text[:117] + "..."
        label = {"user": "用户", "assistant": "助手", "system": "系统"}.get(role, role)
        lines.append(f"- {label}: {text}")
    body = "\n".join(lines[:40])
    return (
        "以下是对话较早部分的压缩摘要，请在后续回复中沿用其中的关键事实与约定：\n"
        + body
    )


def build_summary_prompt_messages(
    to_compact: list[dict],
    previous_summary: str = "",
) -> list[dict]:
    chunks: list[str] = []
    if previous_summary.strip():
        chunks.append(f"【上一轮摘要】\n{previous_summary.strip()}")
    for m in to_compact:
        role = m.get("role") or "user"
        content = m.get("content") or ""
        if isinstance(content, list):
            parts = [
                str(p.get("text") or "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            content = "\n".join(parts)
        text = str(content).strip()
        if not text and m.get("attachments"):
            text = "[含附件]"
        if not text:
            continue
        if len(text) > 2000:
            text = text[:2000] + "…"
        chunks.append(f"{role}: {text}")
    user_body = "\n\n".join(chunks)
    if len(user_body) > 24000:
        user_body = user_body[:24000] + "\n…"
    return [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": user_body or "（无内容）"},
    ]


def append_compaction_point(
    points: list[dict] | None,
    *,
    summary: str,
    boundary_index: int,
    pin_end: int | None = None,
) -> list[dict]:
    out = list(points or [])
    entry: dict = {
        "summary": summary,
        "boundary_index": int(boundary_index),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if pin_end is not None:
        entry["pin_end"] = int(pin_end)
    elif out:
        prev_pin = out[-1].get("pin_end")
        if prev_pin is not None:
            entry["pin_end"] = int(prev_pin)
    out.append(entry)
    return out


def summary_as_message(summary: str) -> dict:
    return {
        "role": "system",
        "content": "【对话摘要】\n" + summary.strip(),
    }
