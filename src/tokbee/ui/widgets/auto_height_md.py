"""自适应高度的 Markdown 浏览器：气泡正文共用组件。

WokBee 时间线与 DeziBee 交互记录都需要「Markdown 渲染 + 高度随内容自适应」的正文区。
尺寸按内容宽度显式 setTextWidth 后测量；内容变化/控件缩放时重算，避免在布局尚未完成
时量到 0 高度而把正文压成一条空条。
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QSizePolicy,
    QTextBrowser,
)

from tokbee.ui.styles.system import make_context_menu
from tokbee.ui.styles.theme import Theme


def stabilize_markdown(text: str) -> str:
    """流式/半截 Markdown 兜底：未闭合的代码围栏补上收尾，避免整段被吞。"""
    if not text:
        return text
    if text.count("```") % 2 == 1:
        return text + "\n```"
    return text


class AutoHeightMd(QTextBrowser):
    """按文档内容自适应高度的 Markdown 浏览器。"""

    def __init__(self, theme: Theme, *, danger: bool = False, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._height_cap = 0
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.setOpenExternalLinks(True)
        self.setReadOnly(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._apply_text_style(danger)
        self.document().contentsChanged.connect(self._update_height)

    def _apply_text_style(self, danger: bool):
        c = self.theme.colors
        color = c.get("danger", "#c0392b") if danger else c["text"]
        self.setStyleSheet(f"""
            QTextBrowser {{
                background: transparent; border: none;
                font-size: 13px; color: {color};
                padding: 0;
            }}
            QTextBrowser a {{ color: {c.get("accent", "#2f6fed")}; }}
        """)

    def set_height_cap(self, cap: int):
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
                w = parent.width() - 8
        return max(w, 160)

    def _update_height(self):
        doc = self.document()
        doc.setTextWidth(self._content_width())
        margins = self.contentsMargins()
        h = int(doc.size().height()) + margins.top() + margins.bottom() + 4
        h = max(h, 24)
        if self._height_cap > 0:
            h = min(h, self._height_cap)
        if abs(h - self.height()) >= 2:
            self.setFixedHeight(h)

    def sizeHint(self) -> QSize:
        return QSize(super().sizeHint().width(), self.height() or 24)

    def set_markdown(self, text: str):
        self.setMarkdown(stabilize_markdown(text or ""))
        self._update_height()

    def set_danger(self, danger: bool):
        self._apply_text_style(danger)

    def contextMenuEvent(self, event):
        menu = make_context_menu(self, self.theme.colors)
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
