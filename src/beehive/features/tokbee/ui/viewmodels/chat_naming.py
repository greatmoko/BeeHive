"""会话临时标题与后台模型命名协调。"""
from PySide6.QtCore import QObject
from beehive.system.prompts import CHAT_TITLE_SYSTEM_PROMPT
from beehive.system.providers.ai_client import AIClient
from beehive.system.providers.provider_store import ProviderStore
from beehive.system.providers.model_settings import settings_for_model
from beehive.features.tokbee.ui.workers.naming import _AiNameWorker


class ChatNamingController(QObject):
    def __init__(self, view):
        super().__init__(view)
        self.vm = view.vm
        self._auto_name_worker = None

    def start(self, session: object):
        """首次提问时，先用前30字做临时标题，再后台调用主模型 AI 命名。"""
        needs_ai = self.vm.auto_title(session)

        if not needs_ai:
            return

        raw = session.messages[0].get("content", "") if session.messages else ""
        if isinstance(raw, list):
            text_parts = [p.get("text", "") for p in raw if isinstance(p, dict) and p.get("type") == "text"]
            raw = " ".join(text_parts)

        primary = self.vm.current_model or ProviderStore().first_resolved()
        if not primary or not primary.api_host:
            return
        client = AIClient(
            primary.api_host, primary.api_key, primary.model_id,
            family=primary.family, protocol=primary.api_protocol,
        )
        messages = [
            {"role": "system", "content": CHAT_TITLE_SYSTEM_PROMPT},
            {"role": "user", "content": raw.strip()[:500]},
        ]
        sid = session.id
        worker = _AiNameWorker(client, messages, settings_for_model(primary), parent=self)
        worker.name_ready.connect(lambda reply, _sid=sid: self.vm.apply_ai_name(_sid, reply))
        worker.failed.connect(lambda _err: None)
        self._auto_name_worker = worker
        worker.finished.connect(worker.deleteLater)
        worker.start()
