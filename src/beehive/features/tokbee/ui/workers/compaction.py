"""按职责组织的 compaction。"""
from __future__ import annotations
import logging
from PySide6.QtCore import Signal, QThread
from beehive.system.providers.ai_client import AIClient
from beehive.system.providers.session_settings import SessionSettings
from beehive.system.memory import context as ctxman
logger = logging.getLogger("tokbee")


class _CompactWorker(QThread):
    """后台线程：生成上下文摘要并返回新的 compaction point。"""
    compact_done = Signal(str, int, int)  # summary, boundary_index, pin_end
    failed = Signal(str)

    def __init__(
        self,
        client: AIClient | None,
        to_compact: list[dict],
        previous_summary: str,
        new_boundary: int,
        parent=None,
        *,
        pin_end: int = 0,
        settings: SessionSettings | None = None,
    ):
        super().__init__(parent)
        self._client = client
        self._to_compact = to_compact
        self._previous_summary = previous_summary
        self._new_boundary = new_boundary
        self._pin_end = int(pin_end or 0)
        self._settings = settings

    def run(self):
        summary = ""
        if self._client is not None:
            try:
                msgs = ctxman.build_summary_prompt_messages(
                    self._to_compact, self._previous_summary,
                )
                resp = self._client.chat(msgs, settings=self._settings)
                summary = (resp.content or "").strip()
                if not summary and resp.reasoning_content:
                    summary = resp.reasoning_content.strip()
            except Exception as e:
                logger.warning("LLM 摘要失败，改用机械摘要: %s", e)
        if not summary:
            summary = ctxman.mechanical_summary(self._to_compact, self._previous_summary)
        if not summary.strip():
            self.failed.emit("无法生成摘要")
            return
        self.compact_done.emit(summary, self._new_boundary, self._pin_end)
