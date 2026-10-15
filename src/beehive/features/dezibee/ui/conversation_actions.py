"""需求会话操作。"""
from __future__ import annotations
import logging
from beehive.features.dezibee.core.models import Conversation
from PySide6.QtCore import QObject

logger = logging.getLogger("dezibee")

class ConversationActions(QObject):
    def __init__(self, view):
        super().__init__(view)
        self.view = view

    def _on_send(self, text: str, attachments: list | None = None):
        req = self.view._current_req()
        if req is None:
            from beehive.common.ui.dialogs.message import show_tip as tip

            tip(self.view, self.view.theme, "请先选择或创建一个需求。")
            return
        if self.view.execution._running_worker(req.id) is not None:
            return  # 该需求已在处理中（其它需求不受影响，可并行）
        text = (text or "").strip()
        attachments = list(attachments or [])
        if not text and not attachments:
            return

        # 追加用户消息到当前对话（附件名一并记录，便于回看）
        conv = req.active_conversation()
        bubble_text = text
        if attachments:
            names = ", ".join(
                str(a.get("display_name") or a.get("path") or "附件")
                for a in attachments
            )
            bubble_text = f"{text}\n\n[附件] {names}" if text else f"（发送了附件：{names}）"
        self.view._append_conv_event(conv, "user", bubble_text)
        self.view.workspace.chat_log._append_bubble("user", bubble_text)
        self.view._persist(req)

        self.view.execution._start_worker(req, text, attachments)

    def _on_new_conversation(self):
        req = self.view._current_req()
        if req is None:
            return
        conv = req.active_conversation()
        # 若当前对话没有任何记录，不重复开空对话
        if not conv.events and len(req.conversations) == 1:
            from beehive.common.ui.dialogs.message import show_tip as tip

            tip(self.view, self.view.theme, "当前对话还没有记录，直接在原对话继续即可。")
            return
        new_conv = Conversation(
            title=f"对话 {len(req.conversations) + 1}",
        )
        req.conversations.append(new_conv)
        req.active_conv_id = new_conv.conv_id
        self.view._persist(req)
        self.view.workspace.set_req(req)
        from beehive.common.ui.dialogs.message import show_tip as tip

        tip(
            self.view,
            self.view.theme,
            "已另起对话（同一需求）。\n"
            "新对话不会继承旧聊天上下文，但仍可读取当前需求信息、Demo 和最新 PRD 文件。",
        )

    def _on_model_changed(self, provider_id: str, model_id: str):
        req = self.view._current_req()
        if req is None:
            return
        req.provider = provider_id
        req.model_id = model_id
        self.view._persist(req)
