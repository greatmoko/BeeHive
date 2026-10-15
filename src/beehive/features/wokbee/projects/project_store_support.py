"""ProjectStore serialization limits and in-memory event cache."""

from __future__ import annotations

import threading

# Cross-thread protection for project metadata and event append/read operations.
_META_LOCK = threading.RLock()
_EVENTS_LOCK = threading.RLock()
_event_lines_cache: dict[str, list[str]] = {}

__all__ = [
    "_META_LOCK",
    "_EVENTS_LOCK",
    "_event_lines_cache",
]
