"""自适应高度的 Markdown 浏览器：气泡正文共用组件。

WokBee 时间线与 DeziBee 交互记录都需要「Markdown 渲染 + 高度随内容自适应」的正文区。
尺寸按内容宽度显式 setTextWidth 后测量；内容变化/控件缩放时重算，避免在布局尚未完成
时量到 0 高度而把正文压成一条空条。
"""

from __future__ import annotations
from beehive.common.ui.markdown.browser_behavior import content_width, fit_content_height, exec_readonly_menu

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QFrame, QSizePolicy, QTextBrowser

from beehive.common.ui.theme import Theme


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
        return content_width(self, minimum=160, parent_padding=8)

    def _update_height(self):
        fit_content_height(self, width=self._content_width(), minimum=24, extra=4, cap=self._height_cap)

    def sizeHint(self) -> QSize:
        return QSize(super().sizeHint().width(), self.height() or 24)

    def set_markdown(self, text: str):
        self.setMarkdown(stabilize_markdown(text or ""))
        self._update_height()

    def set_danger(self, danger: bool):
        self._apply_text_style(danger)

    def contextMenuEvent(self, event):
        exec_readonly_menu(self, event, self.theme.colors)
