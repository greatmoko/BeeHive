"""需求页面装配、列表选择与存储入口。"""
from __future__ import annotations
import logging
from PySide6.QtWidgets import QHBoxLayout, QWidget
from beehive.common.ui.theme import Theme
from beehive.features.dezibee.core.models import Conversation, Requirement
from beehive.features.dezibee.core.store import DeziBeeStore
from beehive.features.dezibee.ui.sidebar import DeziBeeSidebar
from beehive.features.dezibee.ui.workspace import DeziBeeWorkspace
from beehive.features.dezibee.ui.execution_controller import DeziExecutionController
from beehive.features.dezibee.ui.requirement_actions import RequirementActions
from beehive.features.dezibee.ui.conversation_actions import ConversationActions

logger = logging.getLogger("dezibee")

class DeziBeeView(QWidget):
    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._work_root = self._current_work_root()
        self._store = DeziBeeStore()
        self._reqs: list[Requirement] = []
        self.execution = DeziExecutionController(self)
        self.actions = RequirementActions(self)
        self.conversation = ConversationActions(self)
        self._build()
        self._refresh()
        self.sidebar.select_first()

    @staticmethod
    def _current_work_root():
        """当前配置的 DeziBee 工作文件夹（设置页可能随时修改）。"""
        from beehive.features.wokbee.settings import WokBeeSettings

        return WokBeeSettings().dezibee_work_root

    def showEvent(self, event):
        """切回本页时：工作文件夹若被改过，重建 store 并重新加载需求列表。"""
        super().showEvent(event)
        root = self._current_work_root()
        if root != self._work_root:
            self._work_root = root
            self._store = DeziBeeStore(work_root=root)
            self.workspace.set_req(None)
            self._refresh()
            self.sidebar.select_first()

    def _build(self):
        self.setStyleSheet(f"background: {self.theme.colors['content_bg']};")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar = DeziBeeSidebar(self.theme)
        self.sidebar.new_req.connect(self.actions._on_new_req)
        self.sidebar.req_selected.connect(self._on_req_selected)
        self.sidebar.pin_requested.connect(self.actions._on_pin_req)
        self.sidebar.rename_requested.connect(self.actions._on_rename_req)
        self.sidebar.copy_id_requested.connect(self.actions._on_copy_req_id)
        self.sidebar.delete_requested.connect(self.actions._on_delete_req)
        layout.addWidget(self.sidebar)

        self.workspace = DeziBeeWorkspace(self.theme)
        layout.addWidget(self.workspace, 1)

        # 功能及用户输入区（输入框在上，按钮在下）
        bar = self.workspace.input_bar
        bar.preview_clicked.connect(self.actions._on_preview)
        bar.export_clicked.connect(self.actions._on_export)
        bar.new_conversation_clicked.connect(self.conversation._on_new_conversation)
        bar.open_folder_clicked.connect(self.actions._on_open_folder)
        bar.pause_clicked.connect(self.execution._on_pause)
        bar.model_changed.connect(self.conversation._on_model_changed)
        bar.send_clicked.connect(self.conversation._on_send)

    def _refresh(self):
        try:
            self._reqs = self._store.list()
        except Exception as e:
            logger.exception("读取需求失败")
            self._reqs = []
            from beehive.common.ui.dialogs.message import show_tip as tip

            tip(self, self.theme, f"读取需求失败：{e}")
        self.sidebar.set_reqs(self._reqs, set(self.execution._workers.keys()))

    def select_first(self):
        if self._reqs:
            self.sidebar.select(self._reqs[0].id)

    def _on_req_selected(self, req_id: str):
        req = self._store.get(req_id)
        if req is None:
            self.workspace.set_req(None)
            return
        self.workspace.set_req(req)
        # 运行状态只反映当前查看的需求（其它需求可在后台并行运行）
        running = self.execution._running_worker(req_id) is not None
        self.workspace.info_panel.set_running(running)
        self.workspace.input_bar.set_running(running)

    def _append_conv_event(
        self, conv: Conversation, kind: str, content: str, meta: dict | None = None
    ):
        from beehive.features.wokbee.projects.models import ProjectEvent

        ev = ProjectEvent(
            kind=kind,
            content=content,
            meta=dict(meta or {}),
        ).to_dict()
        conv.events.append(ev)
        conv.touch()

    def _persist(self, req: Requirement):
        try:
            self._store.save(req)
        except Exception as e:
            logger.exception("保存需求失败")
            from beehive.common.ui.dialogs.message import show_tip as tip

            tip(self, self.theme, f"保存需求失败：{e}")

    def _current_req(self) -> Requirement | None:
        sid = self.sidebar.current_selected()
        if not sid:
            return None
        return self._store.get(sid)

    def shutdown(self):
        return self.execution.shutdown()
