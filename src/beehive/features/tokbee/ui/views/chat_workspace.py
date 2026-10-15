"""AI 对话页面 — 左侧对话列表 + 右侧对话工作区。"""

from __future__ import annotations


from datetime import datetime


from PySide6.QtCore import Signal, QTimer
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QFrame, QLabel

from beehive.common.ui.theme import Theme
from beehive.common.ui.styles.labels import hint_label_qss, section_label_qss
from beehive.features.tokbee.ui.viewmodels.chat_viewmodel import ChatViewModel
from beehive.features.tokbee.core.chat_manager import ChatManager
from beehive.system.providers.provider_models import ResolvedModel
from beehive.features.tokbee.core.ai_role import AIRoleManager
from beehive.features.tokbee.core.file_reader import is_image, is_document, build_file_filter, save_qimage
from beehive.features.tokbee.ui.views.chat_composer import ChatComposer
from beehive.features.tokbee.ui.viewmodels.chat_requests import ChatRequestController
from beehive.features.tokbee.ui.web_chat import TokBeeChatLog
from beehive.features.tokbee.ui.viewmodels.chat_stream import ChatStreamController
from beehive.features.tokbee.ui.viewmodels.chat_options import ChatOptionsController
from beehive.features.tokbee.ui.viewmodels.chat_compaction import ChatCompactionController
from beehive.features.tokbee.ui.viewmodels.chat_naming import ChatNamingController
from beehive.features.tokbee.ui.views.chat_sidebar import _ChatSidebar


class _ChatWorkspace(QWidget):
    """对话工作区 — 顶部标题 + 消息区 + 底部输入区。"""

    session_created = Signal(str)
    title_changed = Signal(str)  # session_id — 标题被 AI 自动更新后通知 sidebar 刷新

    def __init__(self, theme: Theme, vm: ChatViewModel, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.vm = vm
        self.requests = ChatRequestController(vm, self)
        self.manager = vm.manager
        self._usage_timer = QTimer(self)
        self._usage_timer.setInterval(400)
        self._usage_timer.timeout.connect(self._refresh_context_ring)

        self.vm.title_updated.connect(self._on_vm_title_updated)
        self.vm.model_changed.connect(self._on_vm_model_changed)

        self._build()
        self._usage_timer.start()

    def _build(self):
        c = self.theme.colors
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── 顶部标题栏 ──
        self._header = QFrame()
        self._header.setFixedHeight(48)
        self._header.setStyleSheet(f"""
            QFrame {{
                background: {c["content_bg"]};
                border-bottom: 1px solid {c["border_light"]};
            }}
        """)
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(20, 0, 20, 0)

        self._title_label = QLabel("")
        self._title_label.setStyleSheet(section_label_qss(c))
        header_layout.addWidget(self._title_label)

        self._time_label = QLabel("")
        self._time_label.setStyleSheet(hint_label_qss(c))
        header_layout.addWidget(self._time_label)
        header_layout.addStretch()

        layout.addWidget(self._header)

        # 消息区：复用 WokBee 网页聊天组件（窗口缩放不重排 Qt 部件，解决 resize 卡顿）
        self.chat_log = TokBeeChatLog(self.theme)
        layout.addWidget(self.chat_log, stretch=1)

        self.composer = ChatComposer(
            self.theme,
            accept_file=lambda path: is_image(path) or is_document(path),
            save_image=lambda image: save_qimage(image, self.vm.session.id if self.vm.session else "tmp"),
            file_filter=build_file_filter,
            parent=self,
        )
        self.stream = ChatStreamController(self)
        self.options = ChatOptionsController(self)
        self.compaction = ChatCompactionController(self)
        self.naming = ChatNamingController(self)
        self.vm.error.connect(self.options.tip)
        self.composer.send_requested.connect(self.requests.send)
        self.composer.settings_requested.connect(self.options.session_settings)
        self.composer.model_btn.set_options_provider(self.options.model_options)
        self.composer.model_btn.model_selected.connect(self.vm.select_model)
        self.composer.compress_requested.connect(self.compaction.manual)
        self.composer.files_added.connect(self.vm.add_files)
        self.composer.file_removed.connect(self.vm.remove_file)
        self.vm.files_changed.connect(self.composer.attachments.render)
        self.vm.files_changed.connect(self._refresh_context_ring)
        self.vm.sending_changed.connect(
            lambda sending: self.composer.set_sending(sending, bool(self.vm.session))
        )
        layout.addWidget(self.composer)

        self._show_welcome()


    def load_session(self, session_id: str):
        session = self.manager.get(session_id)
        if not session:
            return

        if self.vm.session:
            self.vm.save_draft(self.composer.input_box.toPlainText())

        self.vm.load_session(session_id)

        saved_draft = self.vm.pop_draft(session_id)
        self.composer.input_box.setPlainText(saved_draft)

        self._header.show()
        self.composer.settings_btn.show()
        self._title_label.setText(session.title)
        try:
            dt = datetime.strptime(session.updated_at, "%Y-%m-%d %H:%M:%S")
            self._time_label.setText(dt.strftime("%Y-%m-%d %H:%M"))
        except ValueError:
            self._time_label.setText(session.updated_at)

        self.stream.detach()
        self.chat_log.clear()
        if session.messages:
            self.chat_log.set_messages(session.messages)

        worker = self.requests.workers.get(session_id)
        if worker and worker.isRunning():
            self.stream.restore(worker)
        else:
            self.vm.set_sending(False)
            if not session.messages:
                self.chat_log.show_empty("开始和 AI 对话吧")

        self.vm.init_model_for_session()
        self._refresh_context_ring()


    def _update_model_btn(self):
        if self.vm.current_model:
            m = self.vm.current_model
            self.composer.model_btn.set_current((m.provider_id, m.model_id))
            ctx = int(m.context_window or 0)
            tip = f"{m.provider_name}/{m.model_id}"
            if ctx > 0:
                tip = f"{tip} {ctx // 1000}k"
            self.composer.model_btn.setToolTip(tip[:30])
        else:
            self.composer.model_btn.set_current(("", ""))
        self._refresh_context_ring()


    def _show_welcome(self):
        """无任何对话被选中时的欢迎页。"""
        self._header.hide()
        self.composer.settings_btn.hide()
        self.stream.detach()

        self.vm.show_welcome_state()
        has_model = bool(self.vm.current_model)

        if has_model:
            self.composer.input_box.setEnabled(True)
            self.composer.input_box.setPlaceholderText("输入消息开始对话…")
            self.composer.send_btn.setEnabled(True)
            self.composer.model_btn.setEnabled(True)
            self._update_model_btn()
        else:
            self.composer.input_box.setEnabled(False)
            self.composer.input_box.setPlaceholderText("请先在 AI配置 → 厂商设置 中添加厂商并勾选模型")
            self.composer.send_btn.setEnabled(False)
            self.composer.model_btn.setEnabled(False)
            self.composer.model_btn.show_placeholder("未选择模型")

        self.chat_log.show_welcome()


    def _refresh_context_ring(self, *_args):
        if not hasattr(self, "composer"):
            return
        usage = self.requests.usage(include_draft=True)
        self.composer.context_ring.set_usage(usage.used, usage.limit)
        busy = self.compaction.busy
        self.composer.context_ring.set_ring_enabled(bool(self.vm.session) and not self.vm.sending and not busy)


    def _on_vm_title_updated(self, session_id: str):
        """ViewModel 通知标题已更新。"""
        if self.vm.session and self.vm.session.id == session_id:
            s = self.manager.get(session_id)
            if s:
                self._title_label.setText(s.title)
        self.title_changed.emit(session_id)

    def _on_vm_model_changed(self, model: ResolvedModel | None):
        self._update_model_btn()
        self._refresh_context_ring()


    def show_welcome(self):
        self.vm.clear_session()
        self._show_welcome()


class ChatView(QWidget):
    """AI 对话页：左侧对话列表 + 右侧对话工作区。"""

    def __init__(self, theme: Theme, role_manager: AIRoleManager | None = None, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._role_manager = role_manager or AIRoleManager()
        self.manager = ChatManager()
        self._vm = ChatViewModel(self.manager, self._role_manager)
        self._build()

    def _build(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._sidebar = _ChatSidebar(self.theme, self.manager)
        self._sidebar.session_selected.connect(self._on_session_selected)
        self._sidebar.session_deleted.connect(self._on_session_deleted)
        layout.addWidget(self._sidebar)

        self._workspace = _ChatWorkspace(self.theme, self._vm)
        self._workspace.session_created.connect(self._on_session_created)
        self._workspace.title_changed.connect(self._on_title_changed)
        layout.addWidget(self._workspace, stretch=1)

        sessions = self.manager.list_sorted()
        if sessions:
            self._sidebar.select(sessions[0].id)

    def _on_session_selected(self, session_id: str):
        self._workspace.load_session(session_id)

    def _on_session_created(self, session_id: str):
        self._sidebar.select(session_id)

    def _on_title_changed(self, session_id: str):
        self._sidebar.refresh()

    def _on_session_deleted(self):
        sessions = self.manager.list_sorted()
        if not sessions:
            self._workspace.show_welcome()

    def refresh_after_clear(self):
        """外部清空聊天记录后刷新 UI。"""
        self._sidebar.refresh()
        sessions = self.manager.list_sorted()
        if sessions:
            self._sidebar.select(sessions[0].id)
        else:
            self._workspace.show_welcome()
