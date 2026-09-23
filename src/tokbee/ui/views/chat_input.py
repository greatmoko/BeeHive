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


class _InputResizeHandle(QWidget):
    """输入框顶部拖拽条：向上拖高、向下拖矮。"""

    drag_delta = Signal(int)  # 正值 = 增高（鼠标上移）

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.setFixedSize(100, 8)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setToolTip("拖动调整高度")
        self._dragging = False
        self._last_y = 0

    def set_available_width(self, width: int):
        self.setFixedWidth(max(28, int(width) // 3))

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = self.theme.colors
        # 拖拽条占可用输入区宽度的 1/3。
        mid_y = self.height() / 2
        x0 = 8
        x1 = self.width() - 8
        pen = QPen(QColor(c.get("border", "#e5e5e5")))
        pen.setWidth(2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(int(x0), int(mid_y), int(x1), int(mid_y))
        p.end()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._last_y = event.globalPosition().toPoint().y()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging:
            y = event.globalPosition().toPoint().y()
            delta = self._last_y - y  # 上移为正 → 增高
            self._last_y = y
            if delta:
                self.drag_delta.emit(delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _ChatInputEdit(QTextEdit):
    """支持粘贴/拖入图片与文件的输入框。"""

    files_dropped = Signal(list)  # list[str] 本地路径

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._session_id_provider = lambda: "tmp"
        self._menu_colors: dict = dict(COLORS)

    def set_menu_colors(self, colors: dict):
        self._menu_colors = colors

    def contextMenuEvent(self, event):
        exec_text_edit_context_menu(self, event, self._menu_colors)

    def set_session_id_provider(self, fn):
        self._session_id_provider = fn

    def insertFromMimeData(self, source: QMimeData):
        paths: list[str] = []
        sid = self._session_id_provider() or "tmp"

        if source.hasUrls():
            for url in source.urls():
                if url.isLocalFile():
                    fp = url.toLocalFile()
                    if is_image(fp) or is_document(fp):
                        paths.append(fp)

        if not paths and source.hasImage():
            img = source.imageData()
            try:
                if isinstance(img, QPixmap) and not img.isNull():
                    paths.append(save_qimage(img, sid))
                elif isinstance(img, QImage) and not img.isNull():
                    paths.append(save_qimage(img, sid))
            except Exception as e:
                logger.error("保存粘贴图片失败: %s", e)

        if paths:
            self.files_dropped.emit(paths)
            # 不把 file:// 路径或文件名再插入输入框
            return

        super().insertFromMimeData(source)

    def dragEnterEvent(self, event):
        md = event.mimeData()
        if md.hasUrls() or md.hasImage():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event):
        md = event.mimeData()
        paths: list[str] = []
        sid = self._session_id_provider() or "tmp"
        if md.hasUrls():
            for url in md.urls():
                if url.isLocalFile():
                    fp = url.toLocalFile()
                    if is_image(fp) or is_document(fp):
                        paths.append(fp)
        if not paths and md.hasImage():
            img = md.imageData()
            try:
                if isinstance(img, (QImage, QPixmap)):
                    paths.append(save_qimage(img, sid))
            except Exception as e:
                logger.error("保存拖入图片失败: %s", e)
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)
