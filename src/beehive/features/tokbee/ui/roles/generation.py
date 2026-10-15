"""按职责组织的 generation。"""
from __future__ import annotations
import logging
from beehive.system.providers.model_settings import settings_for_model
from beehive.system.prompts import ROLE_GENERATION_SYSTEM_PROMPT
from PySide6.QtCore import Signal, QThread
from beehive.system.providers.provider_models import ResolvedModel
from beehive.system.providers.ai_client import AIClient
logger = logging.getLogger("tokbee")


class _RoleGenWorker(QThread):
    """后台线程：调用 AI 生成角色的 System Prompt（非流式）。"""

    done = Signal(str)   # content
    error = Signal(str)


    def __init__(self, model: ResolvedModel, role_name: str,
                 system_prompt: str = "", parent=None):
        super().__init__(parent)
        self._model = model
        self._role_name = role_name
        self._system_prompt = system_prompt or ROLE_GENERATION_SYSTEM_PROMPT

    def run(self):
        try:
            client = AIClient(
                self._model.api_host, self._model.api_key, self._model.model_id,
                family=self._model.family,
                protocol=self._model.api_protocol,
            )
            messages = [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": f"角色名称：{self._role_name}"},
            ]
            resp = client.chat(
                messages,
                settings=settings_for_model(self._model),
            )
            content = resp.content.strip()
            if content:
                self.done.emit(content)
            else:
                self.error.emit("AI 返回了空内容")
        except Exception as e:
            logger.warning("AI 生成角色描述失败: %s", e)
            self.error.emit(str(e))
