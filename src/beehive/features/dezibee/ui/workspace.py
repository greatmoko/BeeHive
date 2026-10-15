"""需求信息、记录和输入工作区装配。"""
from __future__ import annotations
from PySide6.QtWidgets import QVBoxLayout, QWidget
from beehive.common.ui.theme import Theme
from beehive.features.dezibee.core.models import Requirement
from beehive.features.dezibee.ui.info_panel import ReqInfoPanel
from beehive.features.dezibee.ui.web_chat import DeziBeeChatLog

from beehive.features.dezibee.ui.input_bar import InputBar

class DeziBeeWorkspace(QWidget):
    """右侧工作区：需求信息区 / 交互记录区 / 功能及用户输入区（顺序固定）。"""

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._req: Requirement | None = None
        self._build()

    def _build(self):
        self.setStyleSheet(
            f"DeziBeeWorkspace {{ background: {self.theme.colors['content_bg']}; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(0)

        # 1. 需求信息区
        self.info_panel = ReqInfoPanel(self.theme)
        layout.addWidget(self.info_panel)
        layout.addSpacing(8)

        # 2. 交互记录区（可伸缩，WokBee 网页聊天组件）
        self.chat_log = DeziBeeChatLog(self.theme)
        layout.addWidget(self.chat_log, 1)

        # 3+4. 功能及用户输入区（输入框在上，功能按钮在下）
        self.input_bar = InputBar(self.theme)
        layout.addWidget(self.input_bar)

        self.set_req(None)

    def set_req(self, req: Requirement | None):
        """切换当前需求：更新信息区 + 路由交互记录 + 注入 uploads 根目录。"""
        self._req = req
        self.input_bar.set_draft_context(req.id if req else None)
        self.info_panel.set_req(req)
        if req is None:
            self.chat_log.set_conversation(None)
            self.input_bar.reload_models()
            self.input_bar.set_uploads_root(None)
            return
        conv = req.active_conversation()
        self.chat_log.set_conversation(conv)
        # 模型下拉回显需求绑定模型
        self.input_bar.reload_models((req.provider, req.model_id))
        # 粘贴的附件落到需求目录 uploads/
        self.input_bar.set_uploads_root(req.root / "uploads")

    @property
    def req(self) -> Requirement | None:
        return self._req
