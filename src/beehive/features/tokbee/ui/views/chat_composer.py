"""输入区域的布局、键盘、尺寸和附件交互。"""
from PySide6.QtCore import Qt, Signal, QTimer, QEvent
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QPushButton, QFileDialog
from beehive.common.ui.widgets.context_usage import ContextUsageRing
from beehive.common.ui.styles.buttons import apply_icon_btn, apply_primary_btn
from beehive.common.ui.widgets.model_selector import ModelSelectorButton
from beehive.features.tokbee.ui.views.chat_input import _ChatInputEdit
from beehive.common.ui.widgets.execution_input import InputResizeHandle
from beehive.features.tokbee.ui.views.chat_attachment_bar import ChatAttachmentBar

_INPUT_MIN_HEIGHT = 90


class ChatComposer(QFrame):
    send_requested = Signal()
    settings_requested = Signal()
    compress_requested = Signal()
    files_added = Signal(list)
    file_removed = Signal(str)

    def __init__(self, theme, accept_file, save_image, file_filter, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._file_filter = file_filter
        c = theme.colors
        # ── 底部输入区 ──
        input_wrapper = self
        input_wrapper.setStyleSheet(f"""
            QFrame {{
                background: {c["content_bg"]};
                border-top: 1px solid {c["border"]};
            }}
        """)
        input_layout = QVBoxLayout(input_wrapper)
        input_layout.setContentsMargins(20, 2, 20, 14)
        input_layout.setSpacing(4)

        self._input_resize = InputResizeHandle(self.theme)
        self._input_resize.drag_delta.connect(self._on_input_resize_delta)
        input_layout.addWidget(self._input_resize, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.attachments = ChatAttachmentBar(theme, self)
        self.attachments.file_removed.connect(self.file_removed)
        input_layout.addWidget(self.attachments)

        self.input_box = _ChatInputEdit(accept_file, save_image)
        self.input_box.set_menu_colors(c)
        self.input_box.setPlaceholderText("输入消息...（Enter 发送，Shift+Enter 换行，可粘贴图片）")
        self._input_min_h = _INPUT_MIN_HEIGHT
        self.input_box.setMinimumHeight(self._input_min_h)
        self.input_box.setMaximumHeight(self._input_min_h * 8)  # 窗口 resize 时再收紧
        self.input_box.setFixedHeight(self._input_min_h)
        self.input_box.setStyleSheet(f"""
            QTextEdit {{
                background: {c["input_bg"]};
                border: 1px solid {c["input_border"]};
                border-radius: 8px;
                padding: 10px 12px;
                color: {c["text"]};
                font-size: 13px;
            }}
            QTextEdit:focus {{
                border-color: {c["input_focus_border"]};
            }}
        """)
        self.input_box.files_dropped.connect(self.files_added)
        self.input_box.installEventFilter(self)
        input_layout.addWidget(self.input_box)

        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(8)

        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setToolTip("配置当前对话的参数")
        apply_icon_btn(self.settings_btn, c, size=34)
        self.settings_btn.clicked.connect(self.settings_requested)
        bottom_bar.addWidget(self.settings_btn)

        self.model_btn = ModelSelectorButton(self.theme, width=300)
        bottom_bar.addWidget(self.model_btn)

        self.context_ring = ContextUsageRing(self.theme)
        self.context_ring.compress_clicked.connect(self.compress_requested)
        bottom_bar.addWidget(self.context_ring)

        bottom_bar.addStretch()

        self.attach_btn = QPushButton("📎")
        self.attach_btn.setToolTip("上传图片或文档")
        apply_icon_btn(self.attach_btn, c, size=34)
        self.attach_btn.clicked.connect(self._choose_files)
        bottom_bar.addWidget(self.attach_btn)

        self.send_btn = QPushButton("发送")
        self.send_btn.setFixedSize(64, 34)
        apply_primary_btn(self.send_btn, c)
        self.send_btn.clicked.connect(self.send_requested)
        bottom_bar.addWidget(self.send_btn)

        input_layout.addSpacing(4)
        input_layout.addLayout(bottom_bar)


        QTimer.singleShot(0, self._clamp_input_height)
        QTimer.singleShot(0, self._sync_input_resize_width)

    def _choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择文件", "", self._file_filter())
        self.files_added.emit(paths)

    def _input_max_height(self) -> int:
        """软件整体高度的 2/3。"""
        win = self.window()
        h = win.height() if win is not None else self.height()
        if h <= 0:
            h = 600
        return max(self._input_min_h, int(h * 2 / 3))

    def _sync_input_resize_width(self):
        parent = self._input_resize.parentWidget()
        if parent is not None and parent.width() > 0:
            self._input_resize.set_available_width(parent.width())

    def _clamp_input_height(self):
        if not hasattr(self, "input_box"):
            return
        max_h = self._input_max_height()
        self.input_box.setMaximumHeight(max_h)
        cur = self.input_box.height()
        new_h = min(max(cur, self._input_min_h), max_h)
        if new_h != cur:
            self.input_box.setFixedHeight(new_h)

    def _on_input_resize_delta(self, delta: int):
        if not delta:
            return
        max_h = self._input_max_height()
        new_h = min(max(self.input_box.height() + delta, self._input_min_h), max_h)
        self.input_box.setMaximumHeight(max_h)
        self.input_box.setFixedHeight(new_h)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_input_resize_width()
        self._clamp_input_height()

    def eventFilter(self, obj, event):
        if obj is self.input_box and event.type() == QEvent.Type.KeyPress:
            key_event: QKeyEvent = event
            if key_event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if key_event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    return False
                self.send_requested.emit()
                return True
        return super().eventFilter(obj, event)

    def set_sending(self, sending: bool, has_session: bool):
        self.input_box.setEnabled(not sending)
        self.send_btn.setEnabled(not sending)
        self.model_btn.setEnabled(not sending)
        self.attach_btn.setEnabled(not sending)
        if hasattr(self, "context_ring"):
            self.context_ring.set_ring_enabled(not sending and has_session)
        if sending:
            if self.send_btn.text() != "压缩中":
                self.send_btn.setText("…")
        else:
            self.send_btn.setText("发送")
