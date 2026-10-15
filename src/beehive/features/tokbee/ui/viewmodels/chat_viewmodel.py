"""ChatView 的 ViewModel — 将业务逻辑从 View 中分离。"""

from __future__ import annotations

import json as _json
import re

from PySide6.QtCore import QObject, Signal

from beehive.features.tokbee.core.chat_manager import ChatManager, ChatSession
from beehive.system.providers.provider_store import ProviderStore
from beehive.system.providers.provider_models import ResolvedModel
from beehive.system.providers.session_settings import SessionSettings
from beehive.features.tokbee.core.ai_role import AIRole, AIRoleManager

class ChatViewModel(QObject):
    session_changed = Signal(object)
    session_created = Signal(str)
    title_updated = Signal(str)
    model_changed = Signal(object)  # ResolvedModel | None
    sending_changed = Signal(bool)
    files_changed = Signal(list)
    error = Signal(str)

    def __init__(
        self,
        chat_manager: ChatManager,
        role_manager: AIRoleManager,
        provider_store: ProviderStore | None = None,
    ):
        super().__init__()
        self.manager = chat_manager
        self.role_manager = role_manager
        self._provider_store = provider_store or ProviderStore()
        self._session: ChatSession | None = None
        self._current_model: ResolvedModel | None = None
        self._pending_files: list[str] = []
        self._drafts: dict[str, str] = {}
        self._sending = False

    @property
    def provider_store(self) -> ProviderStore:
        return self._provider_store

    @property
    def session(self) -> ChatSession | None:
        return self._session

    @property
    def current_model(self) -> ResolvedModel | None:
        return self._current_model

    @property
    def pending_files(self) -> list[str]:
        return list(self._pending_files)

    def save_draft(self, text: str):
        if self._session:
            self._drafts[self._session.id] = text

    def pop_draft(self, session_id: str) -> str:
        return self._drafts.pop(session_id, "")

    def init_model_for_session(self):
        """根据当前对话已保存的模型初始化；无则用默认/首个可用模型。"""
        self._provider_store = ProviderStore()
        self._current_model = None
        if self._session:
            p = self._session.get_params()
            if p.provider and p.model_id:
                self._current_model = self._provider_store.resolve(p.provider, p.model_id)
            if not self._current_model and self._session.model_name:
                self._current_model = self._provider_store.resolve(
                    self._session.model_provider, self._session.model_name,
                )
        if not self._current_model:
            self._current_model = self._provider_store.first_resolved()
            if self._current_model and self._session:
                params = self._session.get_params()
                params.provider = self._current_model.provider_id
                params.model_id = self._current_model.model_id
                self._session.set_params(params)
                self.manager.save()
        self.model_changed.emit(self._current_model)

    def select_model(self, provider_id: str, model_id: str):
        self._provider_store = ProviderStore()
        selected = self._provider_store.resolve(provider_id, model_id)
        if not selected:
            return
        self._current_model = selected
        self.model_changed.emit(self._current_model)
        if self._session:
            p = self._session.get_params()
            p.provider = selected.provider_id
            p.model_id = selected.model_id
            self._session.set_params(p)
            self.manager.save()

    def load_session(self, session_id: str):
        session = self.manager.get(session_id)
        if not session:
            return
        self._session = session
        self._pending_files.clear()
        self.files_changed.emit(self.pending_files)
        self.session_changed.emit(session)

    def ensure_session(self) -> ChatSession:
        if self._session:
            return self._session
        if not self._current_model:
            raise ValueError("未配置模型")
        session = self.manager.create(
            provider=self._current_model.provider_id,
            model=self._current_model.model_id,
        )
        self._session = session
        self.session_created.emit(session.id)
        return session

    def clear_files(self):
        self._pending_files.clear()
        self.files_changed.emit(self.pending_files)

    def set_sending(self, sending: bool):
        self._sending = sending
        self.sending_changed.emit(sending)

    @property
    def sending(self) -> bool:
        return self._sending

    def add_files(self, paths: list[str]):
        for p in paths:
            if p and p not in self._pending_files:
                self._pending_files.append(p)
        self.files_changed.emit(self.pending_files)

    def remove_file(self, fp: str):
        if fp in self._pending_files:
            self._pending_files.remove(fp)
        self.files_changed.emit(self.pending_files)


    def get_chat_params(self) -> SessionSettings | None:
        if not self._session:
            return None
        return self._session.get_params()

    def save_chat_params(self, params: SessionSettings):
        if self._session:
            self._session.set_params(params)
            self.manager.save()
            if params.provider and params.model_id:
                self.select_model(params.provider, params.model_id)

    def save_stream_result(self, session_id: str, content: str, reasoning: str):
        session = self.manager.get(session_id)
        if not session:
            return
        content, reasoning = self.parse_think_tags(content, reasoning)
        if not content.strip() and reasoning.strip():
            content = reasoning
            reasoning = ""
        msg: dict = {"role": "assistant", "content": content}
        if reasoning:
            msg["reasoning_content"] = reasoning
        session.messages.append(msg)
        self.manager.save()

    def save_sync_result(self, session_id: str, content: str, reasoning: str):
        content, reasoning = self.parse_think_tags(content, reasoning)
        if not content.strip() and reasoning.strip():
            content = reasoning
            reasoning = ""
        session = self.manager.get(session_id)
        if session:
            msg: dict = {"role": "assistant", "content": content}
            if reasoning:
                msg["reasoning_content"] = reasoning
            session.messages.append(msg)
            self.manager.save()

    def auto_title(self, session: ChatSession):
        if not session.title.startswith("对话 "):
            return False
        user_msgs = [m for m in session.messages if m["role"] == "user"]
        if len(user_msgs) != 1:
            return False
        raw = user_msgs[0].get("content", "")
        if isinstance(raw, list):
            text_parts = [
                p.get("text", "") for p in raw
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            raw = " ".join(text_parts)
        fallback = raw.strip().replace("\n", " ")[:30]
        if not fallback:
            return False
        session.title = fallback
        self.title_updated.emit(session.id)
        return True

    def apply_ai_name(self, session_id: str, reply: str):
        name = ""
        text = reply.strip()
        text = re.sub(r'<think>[\s\S]*?</think>', '', text, flags=re.IGNORECASE).strip()
        for m in reversed(list(re.finditer(r'\{[^{}]*\}', text))):
            try:
                obj = _json.loads(m.group())
                name = str(obj.get("name") or "").strip()
                if name:
                    name = name[:30]
                    break
            except (_json.JSONDecodeError, TypeError):
                continue
        if not name:
            return
        s = self.manager.get(session_id)
        if not s:
            return
        s.title = name
        self.manager.save()
        self.title_updated.emit(session_id)

    def create_role(self, name: str, description: str) -> AIRole:
        new_role = AIRole(name=name, description=description)
        self.role_manager.add(new_role)
        return new_role

    @staticmethod
    def parse_think_tags(content: str, reasoning: str) -> tuple[str, str]:
        if "<think>" in content:
            match = re.search(r"<think>(.*?)</think>", content, re.DOTALL)
            if match:
                think_text = match.group(1).strip()
                cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
                if think_text and not reasoning:
                    reasoning = think_text
                return cleaned, reasoning
        return content, reasoning

    def show_welcome_state(self):
        self._provider_store = ProviderStore()
        self._current_model = self._provider_store.first_resolved()
        self._session = None
        self.set_sending(False)
        self.model_changed.emit(self._current_model)

    def clear_session(self):
        self._session = None
