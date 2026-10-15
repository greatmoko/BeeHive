"""原始事件日志与最新轮次切片。"""
from __future__ import annotations

def slice_latest_round(events: list | None) -> list:
    """只保留最新一轮日志。

    新 schema（memory/events.jsonl）：每行都带 round（轮次号），轮次头字段
    只在该轮首事件行上——按 round 分组取最后一组；旧 schema 行仅有 meta
    round_id 时按 round_id 过滤。更旧格式：最后一个「会话结束」标记
    （info 事件 + meta.session_end）与其前一个标记之间的事件。供总结经验时
    过滤旧轮日志，避免把历史轮次塞给 AI 占用 token。
    """
    if not events:
        return list(events or [])

    def _meta(ev) -> dict:
        meta = getattr(ev, "meta", None) or {}
        return meta if isinstance(meta, dict) else {}

    for ev in reversed(events):
        meta = _meta(ev)
        rno = meta.get("round")
        if isinstance(rno, int) and rno >= 1:
            return [e for e in events if _meta(e).get("round") == rno]
        if meta.get("round_id"):
            rid = str(meta["round_id"])
            return [e for e in events if str(_meta(e).get("round_id") or "") == rid]

    def _is_marker(ev) -> bool:
        meta = getattr(ev, "meta", None) or {}
        if not isinstance(meta, dict):
            meta = {}
        return getattr(ev, "kind", "") == "info" and meta.get("session_end")

    markers = [i for i, ev in enumerate(events) if _is_marker(ev)]
    if not markers:
        return list(events)
    if len(markers) >= 2:
        start = markers[-2] + 1
        end = markers[-1]
    else:
        start = 0
        end = markers[-1]
    return list(events[start:end])

def collect_events_log(events: list | None, *, max_chars: int = 20000) -> str:
    """把时间线事件压成日志文本（原文拼接，供 refine 等轻量场景）。

    消息 ID / 时间戳一律改为序数编号（1. 2. 3. …），便于 AI 引用。
    """
    lines: list[str] = []
    for ev in events or []:
        kind = getattr(ev, "kind", "") or ""
        content = (getattr(ev, "content", None) or "").strip()
        if not content:
            continue
        if kind in ("tool", "agent", "error", "user", "info", "approval", "lesson"):
            chunk = content if len(content) <= 1200 else content[:1200] + "…"
            lines.append(f"{len(lines) + 1}. {kind}: {chunk}")
    text = "\n".join(lines) if lines else "（无运行日志）"
    if len(text) > max_chars:
        text = "…(日志前部截断)\n" + text[-max_chars:]
    return text
