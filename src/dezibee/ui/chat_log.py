"""DeziBee 交互记录区：需求关联的消息气泡（用户 / AI / 工具 / 错误）。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from tokbee.ui.styles.theme import Theme

from dezibee.core.models import Conversation


class _Bubble(QFrame):
    """单条消息气泡：左侧角色标签 + 内容区。"""

    def __init__(
        self,
        role: str,  # user | ai | tool | error | info
        content: str,
        theme: Theme,
        parent=None,
    ):
        super().__init__(parent)
        self.theme = theme
        c = theme.colors
        self.setStyleSheet("_Bubble { background: transparent; border: none; }")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 3, 4, 3)
        layout.setSpacing(8)

        role_colors = {
            "user": (c["accent"], c["accent_light"]),
            "ai": (c["text_secondary"], c["card_bg"]),
            "tool": (c["text_hint"], c["tag_bg"]),
            "error": (c["danger"], c["card_bg"]),
            "info": (c["text_hint"], c["tag_bg"]),
        }
        rcolor, bg = role_colors.get(role, (c["text_hint"], c["card_bg"]))

        label_text = {
            "user": "用户",
            "ai": "AI",
            "tool": "工具",
            "error": "错误",
            "info": "系统",
        }.get(role, role)

        tag = QLabel(label_text)
        tag.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        tag.setFixedWidth(44)
        tag.setStyleSheet(
            f"font-size: 11px; color: {rcolor}; font-weight: bold;"
            "background: transparent; border: none;"
        )
        layout.addWidget(tag, 0, Qt.AlignmentFlag.AlignTop)

        if role == "tool":
            # 工具消息：折叠展示摘要
            head = content.splitlines()[0][:120] if content else ""
            body = QLabel(head)
            body.setWordWrap(True)
            body.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            body.setStyleSheet(
                f"font-size: 12px; color: {c['text_secondary']};"
                f"background: {bg}; border-radius: 6px; padding: 6px 10px;"
            )
            layout.addWidget(body, 1)
        else:
            browser = QTextBrowser()
            browser.setOpenExternalLinks(True)
            browser.setStyleSheet(f"""
                QTextBrowser {{
                    background: {bg}; border: none; border-radius: 6px;
                    padding: 8px 10px; font-size: 13px; color: {c['text']};
                }}
            """)
            browser.setMarkdown(content or "")
            browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            # 高度自适应（收紧上限避免整页撑爆）
            doc_height = browser.document().size().height()
            browser.setFixedHeight(min(int(doc_height) + 16, 600))
            layout.addWidget(browser, 1)


class ChatLogPanel(QWidget):
    """交互记录区：纵向滚动消息列表 + 流式增量。"""

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._conversation: Conversation | None = None
        self._stream_target: _Bubble | None = None
        self._build()

    def _build(self):
        self.setStyleSheet("ChatLogPanel { background: transparent; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._container = QWidget()
        self._container.setStyleSheet("background: transparent;")
        self._list_layout = QVBoxLayout(self._container)
        self._list_layout.setContentsMargins(8, 8, 8, 8)
        self._list_layout.setSpacing(6)
        self._list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._scroll.setWidget(self._container)
        layout.addWidget(self._scroll, 1)

    # ── 加载/清空 ────────────────────────────────────────
    def set_conversation(self, conversation: Conversation | None):
        """切换当前需求的当前对话并渲染全部事件。"""
        self._conversation = conversation
        self._clear_all()
        if conversation is None:
            self._append_bubble(
                "info", "选择左侧需求后，在这里与 DeziBee Agent 对话设计产品。"
            )
            return
        self._append_bubble(
            "info", f"对话：{conversation.title}（{conversation.conv_id}）"
        )
        for ev in conversation.events:
            self._render_event(ev)

    def _clear_all(self):
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._stream_target = None

    # ── 事件渲染 ─────────────────────────────────────────
    def _render_event(self, ev: dict):
        kind = (ev.get("kind") or "").strip()
        content = (ev.get("content") or "").strip()
        if not content:
            return
        if kind == "user":
            self._append_bubble("user", content)
        elif kind == "agent":
            self._append_bubble("ai", content)
        elif kind == "error":
            self._append_bubble("error", content)
        elif kind in ("tool", "info"):
            self._append_bubble("tool" if kind == "tool" else "info", content)
        elif kind == "agent_stream":
            pass  # 流式增量不落盘，不入历史
        else:
            self._append_bubble("info", content)

    def _append_bubble(self, role: str, content: str):
        bubble = _Bubble(role, content, self.theme)
        self._list_layout.addWidget(bubble)
        self._scroll_to_bottom()
        return bubble

    def _scroll_to_bottom(self):
        # 使用 QTimer 延迟到主事件循环，确保布局完成
        from PySide6.QtCore import QTimer

        QTimer.singleShot(0, lambda: self._scroll.ensureWidgetVisible(
            self._container, 0, 0
        ))

    # ── 流式增量 ─────────────────────────────────────────
    def begin_stream(self, role: str = "ai"):
        """开启新的 AI 流式气泡。"""
        bubble = _Bubble(role, "", self.theme)
        self._list_layout.addWidget(bubble)
        self._scroll_to_bottom()
        self._stream_target = bubble

    def append_stream(self, delta: str):
        """向当前流式气泡追加内容（整段重设，供简单 UI 使用）。"""
        if self._stream_target is None:
            self.begin_stream("ai")
        target = self._stream_target
        # 往现有气泡内容追加：直接重建气泡文本（简单方案，避免频繁重算高度）
        try:
            browser = target.findChild(QTextBrowser)
            if browser is not None:
                cursor = browser.textCursor()
                cursor.movePosition(QTextCursor.MoveOperation.End)
                cursor.insertText(delta)
                browser.setTextCursor(cursor)
                browser.ensureCursorVisible()
                self._scroll_to_bottom()
        except Exception:
            pass

    def end_stream(self):
        self._stream_target = None
