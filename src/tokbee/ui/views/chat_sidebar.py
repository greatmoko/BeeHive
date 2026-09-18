"""AI 对话页面 — 左侧对话列表 + 右侧对话工作区。"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import logging

from PySide6.QtCore import Qt, Signal, QThread, QEvent, QTimer, QSize, QMimeData
from PySide6.QtGui import QPixmap, QKeyEvent, QImage, QMouseEvent, QPainter, QColor, QPen, QTextDocument, QTextCursor
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QFrame,
    QLabel, QPushButton, QScrollArea, QTextEdit, QTextBrowser,
    QLineEdit, QMenu, QDialog, QSpinBox,
    QComboBox, QFileDialog, QSizePolicy, QApplication,
)

from tokbee.ui.styles.theme import Theme, COLORS
from tokbee.ui.styles.system import (
    make_context_menu,
    exec_text_edit_context_menu,
    rounded_spin_qss,
)
from tokbee.ui.viewmodels.chat_viewmodel import ChatViewModel
from tokbee.ui.widgets.context_ring import ContextUsageRing
from tokbee.core.chat_manager import ChatManager, ChatSession
from tokbee.core.provider_store import ProviderStore, ResolvedModel
from tokbee.core.ai_client import AIClient
from tokbee.core.session_settings import SessionSettings, ProviderOptions
from tokbee.core.ai_role import AIRoleManager
from tokbee.core import context_manager as ctxman
from tokbee.core.file_reader import (
    is_image, is_document, read_image_as_base64, read_file_as_text,
    build_file_filter, save_qimage, persist_attachment,
)

logger = logging.getLogger("tokbee")

_RESOURCES = Path(__file__).parent.parent.parent / "resources"

_INPUT_MIN_HEIGHT = 90  # 当前默认高度


def _model_settings(model: ResolvedModel) -> SessionSettings:
    return SessionSettings(
        temperature=model.temperature,
        top_p=model.top_p,
        max_tokens=model.max_tokens,
        stream=model.stream,
        provider_options=ProviderOptions(
            reasoning_adapter=model.reasoning_adapter,
            reasoning_effort=model.reasoning_effort,
            reasoning_enabled=model.reasoning_enabled,
        ),
    )


def _stabilize_markdown(text: str) -> str:
    """补全未闭合的代码围栏，减轻流式半截 Markdown 导致的高度跳动。"""
    if not text:
        return text
    # 奇数个 ``` 表示围栏未闭合
    if text.count("```") % 2 == 1:
        return text + "\n```"
    return text


class _ChatItem(QFrame):
    """对话列表中的单条项目。"""

    clicked = Signal(str)
    context_menu = Signal(str, object)  # (session_id, QPoint-global)

    def __init__(self, session: ChatSession, theme: Theme, selected: bool = False, parent=None):
        super().__init__(parent)
        self.session = session
        self.theme = theme
        self._selected = selected
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(58)
        self._build()

    def _build(self):
        c = self.theme.colors
        s = self.session

        bg = c["accent_light"] if self._selected else "transparent"
        border = f"border-left: 3px solid {c['accent']};" if self._selected else "border-left: 3px solid transparent;"
        self.setStyleSheet(f"""
            _ChatItem {{
                background: {bg};
                border-radius: 6px;
                {border}
            }}
            _ChatItem:hover {{
                background: {c["subnav_hover"]};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(4)
        if s.pinned:
            pin = QLabel("📌")
            pin.setStyleSheet("font-size: 11px;")
            pin.setFixedWidth(16)
            top.addWidget(pin, 0, Qt.AlignmentFlag.AlignLeft)

        title = QLabel(s.title)
        title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        title.setWordWrap(False)
        title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        title.setStyleSheet(f"""
            font-size: 13px; font-weight: bold; color: {c["text"]};
            background: transparent; border: none;
        """)
        top.addWidget(title, 1)
        layout.addLayout(top)

        try:
            dt = datetime.strptime(s.updated_at, "%Y-%m-%d %H:%M:%S")
            time_str = dt.strftime("%m-%d %H:%M")
        except ValueError:
            time_str = s.updated_at

        info_parts = []
        if s.model_name:
            info_parts.append(s.model_name)
        info_parts.append(time_str)
        info_text = " · ".join(info_parts)

        info = QLabel(info_text)
        info.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        info.setWordWrap(False)
        info.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        info.setStyleSheet(f"font-size: 11px; color: {c['text_hint']}; background: transparent; border: none;")
        layout.addWidget(info)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.session.id)
        elif event.button() == Qt.MouseButton.RightButton:
            self.context_menu.emit(self.session.id, event.globalPosition().toPoint())
        super().mousePressEvent(event)


# ═══════════════════════════════════════════════════════════════
# 对话列表侧边栏
# ═══════════════════════════════════════════════════════════════

class _ChatSidebar(QFrame):
    """二级导航 — 对话列表面板。"""

    session_selected = Signal(str)
    session_deleted = Signal()

    def __init__(self, theme: Theme, chat_manager: ChatManager, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.manager = chat_manager
        self._selected_id: str | None = None
        self._build()
        self.refresh()

    def _build(self):
        c = self.theme.colors
        self.setMinimumWidth(200)
        self.setMaximumWidth(240)
        self.setStyleSheet(f"""
            _ChatSidebar {{
                background: {c["subnav_bg"]};
                border-right: 1px solid {c["border"]};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # 搜索框 + 新建按钮（同一行）
        search_row = QHBoxLayout()
        search_row.setSpacing(6)

        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索对话...")
        self._search.setFixedHeight(30)
        self._search.textChanged.connect(lambda: self.refresh())
        search_row.addWidget(self._search, stretch=1)

        new_btn = QPushButton("＋")
        new_btn.setToolTip("新建对话")
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setFixedSize(30, 30)
        new_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c["btn_bg"]}; color: {c["text"]};
                border: none; border-radius: 6px; font-size: 16px;
            }}
            QPushButton:hover {{ background: {c["btn_hover"]}; }}
        """)
        new_btn.clicked.connect(self._on_new)
        search_row.addWidget(new_btn)

        layout.addLayout(search_row)

        # 列表区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        self._list_container = QWidget()
        self._list_container.setStyleSheet("background: transparent;")
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(2)
        self._list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        scroll.setWidget(self._list_container)
        layout.addWidget(scroll, stretch=1)

    def refresh(self):
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        keyword = self._search.text()
        sessions = self.manager.search(keyword)

        if not sessions:
            c = self.theme.colors
            empty = QLabel("暂无对话")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(f"font-size: 12px; color: {c['text_hint']}; padding: 20px 0;")
            self._list_layout.addWidget(empty)
            return

        for s in sessions:
            item = _ChatItem(s, self.theme, selected=(s.id == self._selected_id))
            item.clicked.connect(self._on_select)
            item.context_menu.connect(self._on_context_menu)
            self._list_layout.addWidget(item)

    def select(self, session_id: str):
        self._selected_id = session_id
        self.refresh()
        self.session_selected.emit(session_id)

    def _on_select(self, session_id: str):
        self._selected_id = session_id
        self.refresh()
        self.session_selected.emit(session_id)

    def _on_new(self):
        # 新建对话：预填默认模型（或首个可用模型）
        store = ProviderStore()
        primary = store.first_resolved()
        session = self.manager.create(
            provider=primary.provider_id if primary else "",
            model=primary.model_id if primary else "",
        )
        self._selected_id = session.id
        self.refresh()
        self.session_selected.emit(session.id)

    def _on_context_menu(self, session_id: str, pos):
        c = self.theme.colors
        session = self.manager.get(session_id)
        if not session:
            return

        menu = make_context_menu(self, c)

        pin_text = "取消置顶" if session.pinned else "置顶"
        pin_action = menu.addAction(pin_text)
        rename_action = menu.addAction("重命名")
        menu.addSeparator()
        delete_action = menu.addAction("删除")
        delete_action.setProperty("color", c["danger"])

        action = menu.exec(pos)
        if action == pin_action:
            self.manager.toggle_pin(session_id)
            self.refresh()
        elif action == rename_action:
            self._show_rename_dialog(session_id, session.title)
        elif action == delete_action:
            self._show_delete_dialog(session_id)

    def _show_rename_dialog(self, session_id: str, old_title: str):
        c = self.theme.colors
        dlg = QDialog(self)
        dlg.setWindowTitle("重命名对话")
        dlg.setFixedSize(360, 170)
        dlg.setStyleSheet(f"background: {c['content_bg']};")

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(12)

        label = QLabel("新名称")
        label.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {c['text']};")
        layout.addWidget(label)

        inp = QLineEdit(old_title)
        inp.setFixedHeight(36)
        inp.selectAll()
        layout.addWidget(inp)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setFixedSize(72, 34)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {c["text_secondary"]};
                border: 1px solid {c["border"]}; border-radius: 6px; font-size: 13px;
            }}
            QPushButton:hover {{ background: {c["subnav_hover"]}; }}
        """)
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(cancel_btn)

        ok_btn = QPushButton("确定")
        ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_btn.setFixedSize(72, 34)
        ok_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c["btn_bg"]}; color: {c["text"]};
                border: none; border-radius: 6px; font-size: 13px;
            }}
            QPushButton:hover {{ background: {c["btn_hover"]}; }}
        """)
        ok_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(ok_btn)

        layout.addLayout(btn_row)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_title = inp.text().strip()
            if new_title:
                self.manager.rename(session_id, new_title)
                self.refresh()
                self.session_selected.emit(session_id)

    def _show_delete_dialog(self, session_id: str):
        c = self.theme.colors
        dlg = QDialog(self)
        dlg.setWindowTitle("确认删除")
        dlg.setFixedSize(340, 150)
        dlg.setStyleSheet(f"background: {c['content_bg']};")

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(16)

        msg = QLabel("确定删除这个对话吗？删除后无法恢复。")
        msg.setWordWrap(True)
        msg.setStyleSheet(f"font-size: 14px; color: {c['text']};")
        layout.addWidget(msg)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setFixedSize(72, 34)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {c["text_secondary"]};
                border: 1px solid {c["border"]}; border-radius: 6px; font-size: 13px;
            }}
            QPushButton:hover {{ background: {c["subnav_hover"]}; }}
        """)
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(cancel_btn)

        del_btn = QPushButton("删除")
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.setFixedSize(72, 34)
        del_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c["danger"]}; color: #ffffff;
                border: none; border-radius: 6px; font-size: 13px;
            }}
            QPushButton:hover {{ background: #d32f2f; }}
        """)
        del_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(del_btn)

        layout.addLayout(btn_row)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            was_selected = session_id == self._selected_id
            self.manager.delete(session_id)
            if was_selected:
                remaining = self.manager.list_sorted()
                self._selected_id = remaining[0].id if remaining else None
            self.refresh()
            self.session_deleted.emit()
            if self._selected_id:
                self.session_selected.emit(self._selected_id)
