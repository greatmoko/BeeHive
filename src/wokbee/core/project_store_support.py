"""ProjectStore serialization limits and in-memory event cache."""

from __future__ import annotations

import threading

from wokbee.core.models import ProjectEvent

# Cross-thread protection for project metadata and event append/read operations.
_META_LOCK = threading.RLock()
_EVENTS_LOCK = threading.RLock()
_event_lines_cache: dict[str, list[str]] = {}

# Keep complete tool output in the Agent context; cap only persisted event text.
EVENT_LOG_MAX_CHARS = 32_000


def _event_for_log(event: ProjectEvent) -> dict:
    """Return a JSONL-safe event copy without mutating the live event."""
    data = event.to_dict()
    content = str(data.get("content") or "")
    if len(content) <= EVENT_LOG_MAX_CHARS:
        return data
    marker = (
        f"\n…（日志内容已截断，原始 {len(content)} 字符，"
        f"上限 {EVENT_LOG_MAX_CHARS} 字符）"
    )
    head_size = max(0, EVENT_LOG_MAX_CHARS - len(marker))
    data["content"] = content[:head_size] + marker
    meta = dict(data.get("meta") or {})
    meta["log_truncated"] = True
    meta["original_content_length"] = len(content)
    data["meta"] = meta
    return data


__all__ = [
    "_META_LOCK",
    "_EVENTS_LOCK",
    "_event_lines_cache",
    "EVENT_LOG_MAX_CHARS",
    "_event_for_log",
]
