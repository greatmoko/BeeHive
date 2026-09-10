"""DeziBee 工作区（右侧）：需求信息区 + 交互记录区 + 功能区 + 用户输入区。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from tokbee.ui.combo_style import apply_combo_popup_style
from tokbee.ui.styles.theme import Theme

from dezibee.core.models import Requirement
from dezibee.core.services import model_options
from dezibee.ui.chat_log import ChatLogPanel
from dezibee.ui.info_panel import ReqInfoPanel


class ActionBar(QFrame):
    """功能区：预览 / 总结上下文 / 另起对话 / AI 模型选择。"""

    preview_clicked = Signal()
    summarize_clicked = Signal()
    new_conversation_clicked = Signal()
    model_changed = Signal(str, str)  # provider_id, model_id

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        c = theme.colors
        self.setStyleSheet(
            f"ActionBar {{ background: {c['card_bg']}; border-radius: 8px; }}"
        )
        self.setFixedHeight(44)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)

        self._btn_style = f"""
            QPushButton {{
                background: {c["btn_bg"]}; color: {c["text"]};
                border: none; border-radius: 6px; padding: 0 12px; font-size: 12px;
            }}
            QPushButton:hover {{ background: {c["btn_hover"]}; }}
        """

        preview = QPushButton("预览")
        preview.setCursor(Qt.CursorShape.PointingHandCursor)
        preview.setFixedHeight(30)
        preview.setStyleSheet(f"""
            QPushButton {{
                background: {c["btn_primary"]}; color: #ffffff;
                border: none; border-radius: 6px; padding: 0 14px; font-size: 12px;
            }}
            QPushButton:hover {{ background: {c["btn_primary_hover"]}; }}
        """)
        preview.clicked.connect(self.preview_clicked.emit)
        layout.addWidget(preview)

        summarize = QPushButton("总结上下文")
        summarize.setCursor(Qt.CursorShape.PointingHandCursor)
        summarize.setFixedHeight(30)
        summarize.setStyleSheet(self._btn_style)
        summarize.clicked.connect(self.summarize_clicked.emit)
        layout.addWidget(summarize)

        new_conv = QPushButton("另起对话")
        new_conv.setCursor(Qt.CursorShape.PointingHandCursor)
        new_conv.setFixedHeight(30)
        new_conv.setStyleSheet(self._btn_style)
        new_conv.clicked.connect(self.new_conversation_clicked.emit)
        layout.addWidget(new_conv)

        layout.addStretch()

        model_lbl = QLabel("AI模型")
        model_lbl.setStyleSheet(
            f"font-size: 12px; color: {c['text_secondary']}; background: transparent;"
        )
        layout.addWidget(model_lbl)

        self._model_combo = QComboBox()
        apply_combo_popup_style(self._model_combo, c, rounded=True, fixed_width=210, fixed_height=30)
        self._model_combo.currentIndexChanged.connect(self._on_model_selected)
        layout.addWidget(self._model_combo)
        self.model_combo = self._model_combo  # 供外部 reload_models / 回显需求模型

        self.reload_models()

    def reload_models(self, selected: tuple[str, str] | None = None):
        """刷新模型下拉；selected=(provider_id, model_id) 需保持选中。"""
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        self._model_combo.addItem("（默认模型）", ("", ""))
        index = 0
        for i, (label, pair) in enumerate(model_options(), start=1):
            self._model_combo.addItem(label, pair)
            if selected and pair == tuple(selected):
                index = i
        self._model_combo.setCurrentIndex(index)
        self._model_combo.blockSignals(False)

    def _on_model_selected(self):
        pair = self._model_combo.currentData() or ("", "")
        self.model_changed.emit(str(pair[0]), str(pair[1]))

    def set_running(self, running: bool):
        """运行中禁用模型切换，避免中途改模型。"""
        self._model_combo.setEnabled(not running)


class InputBar(QFrame):
    """用户输入区（多行输入 + 发送）。"""

    send_clicked = Signal(str)

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        c = theme.colors
        self.setStyleSheet(
            f"InputBar {{ background: {c['content_bg']}; border-top: 1px solid {c['border']}; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 10)
        layout.setSpacing(6)

        self._edit = QTextEdit()
        self._edit.setPlaceholderText(
            "输入需求或修改意见，Enter 发送；Ctrl+Enter 换行…"
        )
        self._edit.setAcceptRichText(False)
        self._edit.setFixedHeight(64)
        self._edit.setStyleSheet(f"""
            QTextEdit {{
                background: {c["input_bg"]}; color: {c["text"]};
                border: 1px solid {c["input_border"]}; border-radius: 8px;
                padding: 6px 10px; font-size: 13px;
            }}
            QTextEdit:focus {{ border: 1px solid {c["input_focus_border"]}; }}
        """)
        layout.addWidget(self._edit)

        row = QHBoxLayout()
        row.addStretch()
        self._status = QLabel("")
        self._status.setStyleSheet(
            f"font-size: 11px; color: {c['text_hint']}; background: transparent;"
        )
        row.addWidget(self._status)
        self._send_btn = QPushButton("发送")
        self._send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send_btn.setFixedSize(72, 32)
        self._send_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c["btn_primary"]}; color: #ffffff;
                border: none; border-radius: 6px; font-size: 13px;
            }}
            QPushButton:hover {{ background: {c["btn_primary_hover"]}; }}
            QPushButton:disabled {{ background: {c["btn_bg"]}; color: {c["text_hint"]}; }}
        """)
        self._send_btn.clicked.connect(self._on_send)
        row.addWidget(self._send_btn)
        layout.addLayout(row)

    def _on_send(self):
        text = self._edit.toPlainText().strip()
        if not text:
            return
        self.send_clicked.emit(text)
        self._edit.clear()

    def set_running(self, running: bool):
        self._send_btn.setEnabled(not running)
        self._status.setText("Agent 处理中…" if running else "")

    def focus_input(self):
        self._edit.setFocus()


class DeziBeeWorkspace(QWidget):
    """右侧工作区：需求信息区 / 交互记录区 / 功能区 / 用户输入区（顺序固定）。"""

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
        layout.setContentsMargins(12, 12, 12, 0)
        layout.setSpacing(8)

        # 1. 需求信息区
        self.info_panel = ReqInfoPanel(self.theme)
        layout.addWidget(self.info_panel)

        # 2. 交互记录区（可伸缩）
        self.chat_log = ChatLogPanel(self.theme)
        layout.addWidget(self.chat_log, 1)

        # 3. 功能区
        self.action_bar = ActionBar(self.theme)
        layout.addWidget(self.action_bar)

        # 4. 用户输入区
        self.input_bar = InputBar(self.theme)
        layout.addWidget(self.input_bar)

        self.set_req(None)

    def set_req(self, req: Requirement | None):
        """切换当前需求：更新信息区 + 路由交互记录到该需求当前对话。"""
        self._req = req
        self.info_panel.set_req(req)
        if req is None:
            self.chat_log.set_conversation(None)
            self.action_bar.reload_models()
            return
        conv = req.active_conversation()
        self.chat_log.set_conversation(conv)
        # 模型下拉回显需求绑定模型
        self.action_bar.reload_models((req.provider, req.model_id))

    @property
    def req(self) -> Requirement | None:
        return self._req
