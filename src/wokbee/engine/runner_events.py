"""线程安全的运行事件缓冲与 UI 回调转发。"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from types import SimpleNamespace

from wokbee.core.credential_store import redact_obj, redact_text


EventCallback = Callable[[str, str, dict], None]
logger = logging.getLogger("wokbee")


class ExecutionEventLog:
    def __init__(self) -> None:
        self._items: list[SimpleNamespace] = []
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._items = []

    def snapshot(self) -> list:
        with self._lock:
            return list(self._items)

    def emit(self, callback: EventCallback | None, kind: str, content: str, meta: dict | None = None) -> None:
        meta = dict(meta or {})
        content = redact_text(content or "")
        if "args" in meta:
            meta["args"] = redact_obj(meta["args"])
        with self._lock:
            self._items.append(SimpleNamespace(kind=kind, content=content, meta=dict(meta)))
        if callback:
            try:
                callback(kind, content, meta)
            except Exception:
                logger.exception("on_event 回调失败")

    def emit_stream(self, callback: EventCallback | None, target: str, delta: str) -> None:
        if not delta or not callback:
            return
        try:
            callback("agent_stream", delta, {"target": target})
        except Exception:
            logger.exception("agent_stream 事件回调失败")
