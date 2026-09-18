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


class _AutoHeightBrowser(QTextBrowser):
    """QTextBrowser that auto-sizes its height to fit all content."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setOpenExternalLinks(True)
        self.setReadOnly(True)
        self._menu_colors: dict = dict(COLORS)
        # 流式高度上限：避免半截 Markdown / 宽度为 0 时算出天文数字高度
        self._height_cap = 0
        self.document().contentsChanged.connect(self._update_height)

    def set_menu_colors(self, colors: dict):
        self._menu_colors = colors

    def contextMenuEvent(self, event):
        menu = make_context_menu(self, self._menu_colors)
        cursor = self.textCursor()
        has_sel = cursor.hasSelection()
        has_text = bool(self.toPlainText())
        copy_act = menu.addAction("复制")
        copy_act.setShortcut("Ctrl+C")
        copy_act.setEnabled(has_sel or has_text)
        link = self.anchorAt(event.pos())
        copy_link_act = None
        if link:
            copy_link_act = menu.addAction("复制链接")
        menu.addSeparator()
        select_all_act = menu.addAction("全选")
        select_all_act.setEnabled(has_text)
        action = menu.exec(event.globalPos())
        if action == copy_act:
            if has_sel:
                text = cursor.selectedText().replace("\u2029", "\n")
            else:
                text = self.toPlainText()
            QApplication.clipboard().setText(text)
        elif copy_link_act and action == copy_link_act:
            QApplication.clipboard().setText(link)
        elif action == select_all_act:
            cursor.select(QTextCursor.SelectionType.Document)
            self.setTextCursor(cursor)
        event.accept()

    def set_height_cap(self, cap: int):
        """cap<=0 表示不限制（结束后收紧到真实内容高度）。"""
        self._height_cap = max(0, int(cap))
        self._update_height()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_height()

    def _content_width(self) -> int:
        w = self.viewport().width()
        if w <= 1:
            w = self.width()
        if w <= 1:
            parent = self.parentWidget()
            if parent is not None:
                w = parent.width() - 24
        return max(w, 200)

    def _update_height(self):
        doc = self.document()
        doc.setTextWidth(self._content_width())
        margins = self.contentsMargins()
        h = int(doc.size().height()) + margins.top() + margins.bottom() + 2 * self.frameWidth()
        h = max(h, 30)
        if self._height_cap > 0:
            h = min(h, self._height_cap)
        # 仅在变化明显时改高度，减少微抖
        if abs(h - self.height()) >= 2:
            self.setFixedHeight(h)

    def sizeHint(self) -> QSize:
        return QSize(super().sizeHint().width(), self.minimumHeight() or 30)


class _UserBubbleLabel(QLabel):
    """用户气泡：按最大宽度正确换行并计算高度，避免长文被裁切。"""

    _H_PAD = 28  # 左右 padding 估算
    _V_PAD = 24  # 上下 padding 估算

    def __init__(self, text: str, max_width: int, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._max_w = max(160, int(max_width))
        font = self.font()
        font.setPixelSize(14)
        self.setFont(font)
        self.setText(text)
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Minimum)
        self.setStyleSheet(f"""
            QLabel {{
                background: #07c160;
                border-radius: 12px;
                border-top-right-radius: 4px;
                padding: 10px 14px;
                font-size: 14px;
                color: #ffffff;
            }}
        """)
        self.setMaximumWidth(self._max_w)
        self._apply_size()

    def contextMenuEvent(self, event):
        menu = make_context_menu(self, self.theme.colors)
        copy_act = menu.addAction("复制")
        copy_act.setShortcut("Ctrl+C")
        copy_act.setEnabled(bool(self.text()))
        action = menu.exec(event.globalPos())
        if action == copy_act:
            QApplication.clipboard().setText(self.text())
        event.accept()

    def set_max_bubble_width(self, max_width: int):
        self._max_w = max(160, int(max_width))
        self.setMaximumWidth(self._max_w)
        self._apply_size()

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._calc_size(width)[1]

    def sizeHint(self) -> QSize:
        w, h = self._calc_size(self._max_w)
        return QSize(w, h)

    def minimumSizeHint(self) -> QSize:
        return QSize(60, 36)

    def _calc_size(self, max_width: int) -> tuple[int, int]:
        max_width = max(80, int(max_width))
        doc = QTextDocument()
        doc.setDefaultFont(self.font())
        doc.setPlainText(self.text())
        content_max = max(40, max_width - self._H_PAD)
        # idealWidth：不换行时的自然宽度
        doc.setTextWidth(-1)
        ideal = int(doc.idealWidth()) + 4
        content_w = min(content_max, max(ideal, 40))
        doc.setTextWidth(content_w)
        h = int(doc.size().height()) + self._V_PAD
        w = content_w + self._H_PAD
        return max(60, w), max(36, h)

    def _apply_size(self):
        w, h = self._calc_size(self._max_w)
        self.setFixedSize(w, h)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 父级变窄时收紧
        parent = self.parentWidget()
        if parent is not None and parent.width() > 0:
            avail = parent.width()
            if avail < self._max_w:
                self._max_w = max(160, avail)
                self.setMaximumWidth(self._max_w)
                self._apply_size()
