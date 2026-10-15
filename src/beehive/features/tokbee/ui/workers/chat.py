"""按职责组织的 chat。"""
from __future__ import annotations
import logging
from beehive.system.providers.model_settings import apply_model_settings
from PySide6.QtCore import Signal, QThread
from beehive.system.providers.provider_models import ResolvedModel
from beehive.system.providers.ai_client import AIClient
from beehive.system.providers.session_settings import SessionSettings
logger = logging.getLogger("tokbee")


class _AIChatWorker(QThread):
    """后台线程：调用 AI chat/completions API（支持流式/非流式）。"""

    chunk_received = Signal(str, str, str)   # (session_id, content_delta, reasoning_delta)
    non_stream_done = Signal(str, str, str)  # (session_id, content, reasoning_content)
    stream_done = Signal(str)                # (session_id,)
    error = Signal(str, str)                 # (session_id, error_msg)

    def __init__(self, session_id: str, model: ResolvedModel,
                 messages: list[dict], settings: SessionSettings,
                 parent=None):
        super().__init__(parent)
        self.session_id = session_id
        self._model = model
        self._messages = messages
        self._settings = SessionSettings.from_dict(settings.to_dict())
        apply_model_settings(model, self._settings)
        self._stream = model.stream
        self._cancelled = False
        self.accumulated_content = ""
        self.accumulated_reasoning = ""

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            client = AIClient(
                self._model.api_host, self._model.api_key, self._model.model_id,
                family=self._model.family,
                protocol=self._model.api_protocol,
            )
            # 让同步请求的等待/退避可被取消（A7）
            client.cancel_check = lambda: self._cancelled
            if self._stream:
                self._run_stream(client)
            else:
                self._run_sync(client)
        except Exception as e:
            logger.error("AI API 调用失败: %s", e)
            self.error.emit(self.session_id, str(e))

    def _run_stream(self, client: AIClient):
        has_content = False
        try:
            for chunk in client.chat_stream(self._messages, settings=self._settings):
                if self._cancelled:
                    return
                if chunk.is_finished:
                    break
                if chunk.content_delta or chunk.reasoning_delta:
                    has_content = True
                    self.accumulated_content += chunk.content_delta
                    self.accumulated_reasoning += chunk.reasoning_delta
                    self.chunk_received.emit(self.session_id, chunk.content_delta, chunk.reasoning_delta)
            if not self._cancelled:
                self.stream_done.emit(self.session_id)
        except Exception as e:
            logger.error("AI 流式调用异常: %s", e)
            if has_content:
                # 已有部分内容：不再当完整回答落盘，标记为「已中断」由 error 路径保留
                self.error.emit(self.session_id, f"流式中断（保留部分内容）：{e}")
            else:
                raise

    def _run_sync(self, client: AIClient):
        resp = client.chat(self._messages, settings=self._settings)
        if self._cancelled:
            return
        self.non_stream_done.emit(self.session_id, resp.content, resp.reasoning_content)
