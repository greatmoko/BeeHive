"""DeziBee 交互记录区：需求关联的消息气泡（用户 / AI / 工具 / 错误）。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from tokbee.ui.styles.theme import Theme
from tokbee.ui.widgets.auto_height_md import AutoHeightMd

from dezibee.core.models import Conversation

# 工具气泡只展示首行摘要：write_file 等正文极长，避免撑爆交互记录
TOOL_SUMMARY_CHARS = 120


def _tool_summary(content: str) -> str:
    """工具气泡正文：取首行（**call:** `name` / **callback:** `name`）作为摘要。"""
    text = content or ""
    if not text.strip():
        return ""
    return text.splitlines()[0][:TOOL_SUMMARY_CHARS]


class _Bubble(QFrame):
    """单条消息气泡：左侧角色标签 + 右侧内容卡片（Markdown、高度自适应）。"""

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

        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background: {bg}; border: none; border-radius: 6px; }}"
        )
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(10, 6, 10, 6)
        card_lay.setSpacing(0)
        self._browser = AutoHeightMd(theme, danger=(role == "error"))
        card_lay.addWidget(self._browser)
        layout.addWidget(card, 1)

        self._raw = ""
        self.set_content(_tool_summary(content) if role == "tool" else content)

    def set_content(self, content: str):
        """整段设置气泡正文（Markdown）。"""
        self._raw = content or ""
        self._browser.set_markdown(self._raw)

    def append_stream(self, delta: str):
        """流式增量：累积后整段重渲染，Markdown 与高度随内容同步。"""
        if not delta:
            return
        self._raw += delta
        self._browser.set_markdown(self._raw)


class ChatLogPanel(QWidget):
    """交互记录区：纵向滚动消息列表 + 流式增量。"""

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._conversation: Conversation | None = None
        self._stream_targets: dict[str, _Bubble] = {}
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
        self._stream_targets.clear()

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

    def _append_bubble(self, role: str, content: str) -> _Bubble:
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
    def append_stream(self, delta: str, target: str = "text"):
        """向对应流式气泡追加增量（reasoning / text 各一条，互不串行）。"""
        target = "reasoning" if target == "reasoning" else "text"
        if not delta:
            return
        bubble = self._stream_targets.get(target)
        if bubble is None:
            bubble = self._append_bubble("ai", "")
            self._stream_targets[target] = bubble
        bubble.append_stream(delta)
        self._scroll_to_bottom()

    def finalize_stream(self, target: str, content: str) -> bool:
        """完整 agent 事件到达：把对应流式气泡原地落为最终内容并停止更新。

        返回 False 表示该 target 没有进行中的流式气泡（调用方按新增气泡处理）。
        """
        target = "reasoning" if target == "reasoning" else "text"
        bubble = self._stream_targets.pop(target, None)
        if bubble is None:
            return False
        bubble.set_content(content)
        return True

    def end_stream(self):
        self._stream_targets.clear()
