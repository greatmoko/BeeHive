"""按职责组织的 naming。"""
from __future__ import annotations
from PySide6.QtCore import Signal, QThread
from beehive.system.providers.ai_client import AIClient
from beehive.system.providers.session_settings import SessionSettings


class _AiNameWorker(QThread):
    """后台线程：调用 AI 生成对话标题。"""
    name_ready = Signal(str)
    failed = Signal(str)

    def __init__(self, client: AIClient, messages: list[dict], settings: SessionSettings, parent=None):
        super().__init__(parent)
        self._client = client
        self._messages = messages
        self._settings = settings

    def run(self):
        try:
            resp = self._client.chat(self._messages, settings=self._settings)
            content = resp.content or ""
            if not content.strip() and resp.reasoning_content:
                content = resp.reasoning_content
            self.name_ready.emit(content)
        except Exception as e:
            self.failed.emit(str(e))
