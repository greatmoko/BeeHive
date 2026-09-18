"""时间线卡片组件：正文、实时状态、思考块和工具步骤。"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from tokbee.ui.styles.theme import Theme
from tokbee.ui.widgets.auto_height_md import AutoHeightMd


def _redact_display(text: str) -> str:
    try:
        from wokbee.core.credential_store import redact_text

        return redact_text(text or "")
    except Exception:
        return text or ""


class _ExpandableBody(QWidget):
    """正文：Markdown 渲染；默认高度上限，可展开全部。"""

    def __init__(
        self,
        text: str,
        theme: Theme,
        *,
        danger: bool = False,
        default_collapsed: bool = False,
        toggle_text: str = "",
        hide_toggle: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.theme = theme
        self._full = text or ""
        self._danger = danger
        self._default_collapsed = default_collapsed
        self._toggle_text = toggle_text
        self._hide_toggle = hide_toggle
        self._expanded = not default_collapsed
        self._global_compact = False
        self._manual_override = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        c = theme.colors
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self._browser = AutoHeightMd(theme, danger=danger, parent=self)
        lay.addWidget(self._browser)
        self._toggle = QPushButton()
        self._toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle.setFlat(True)
        self._toggle.setStyleSheet(
            f"QPushButton {{ color: {c['accent']}; font-size: 12px; border: none; "
            f"text-align: left; padding: 0; background: transparent; }}"
            f"QPushButton:hover {{ color: {c.get('accent_hover', '#06ad56')}; "
            f"background: transparent; }}"
            f"QPushButton:pressed {{ color: {c.get('accent_hover', '#06ad56')}; "
            f"background: transparent; }}"
        )
        self._toggle.clicked.connect(self._on_toggle)
        lay.addWidget(self._toggle)
        self._apply()

    def refresh_height(self):
        # QTimer.singleShot(0/30, ...) 可能在 deleteLater 后触发，跳过已销毁的 browser。
        try:
            self._browser._update_height()
        except RuntimeError:
            return

    def set_global_compact(self, compact: bool, *, reset_manual: bool = True) -> None:
        """全局收拢/展开：收拢时统一约一行正文，用户可单独展开某条。"""
        self._global_compact = bool(compact)
        if reset_manual:
            self._manual_override = False
        if self._global_compact:
            self._expanded = bool(self._manual_override)
        else:
            self._expanded = not self._default_collapsed
        self._apply()
        QTimer.singleShot(0, self.refresh_height)
        QTimer.singleShot(30, self.refresh_height)

    def _needs_expand(self) -> bool:
        full = self._full
        if not full:
            return False
        if self._global_compact and not self._manual_override:
            return (
                len(full.splitlines()) > 1
                or len(full) > BUBBLE_COMPACT_CHARS
            )
        if self._default_collapsed:
            return True
        _, need = _preview_text(full)
        if need:
            return True
        # 行少但 Markdown 渲染后仍可能很高：用折叠高度兜底
        return len(full) > BUBBLE_PREVIEW_CHARS or len(full.splitlines()) > BUBBLE_PREVIEW_LINES

    def set_content(self, text: str, *, danger: bool | None = None) -> None:
        """原位替换内容/配色（工具步骤行用），保持折叠状态。"""
        self._full = text or ""
        if danger is not None and danger != self._danger:
            self._danger = danger
            self._browser.set_danger(danger)
        self._apply()
        QTimer.singleShot(0, self.refresh_height)
        QTimer.singleShot(30, self.refresh_height)

    def _apply(self):
        full = self._full
        need = self._needs_expand()
        toggle_visible = need and not self._hide_toggle
        if self._global_compact and not self._manual_override:
            self._browser.set_height_cap(BUBBLE_COMPACT_HEIGHT)
            self._browser.set_markdown(full)
            self._toggle.setVisible(toggle_visible)
            self._toggle.setText("展开" if need else "")
        elif not need or self._expanded:
            self._browser.set_height_cap(0)
            self._browser.set_markdown(full)
            self._toggle.setVisible(toggle_visible)
            self._toggle.setText("收起" if need and not self._global_compact else "")
        else:
            self._browser.set_height_cap(BUBBLE_COLLAPSED_HEIGHT)
            self._browser.set_markdown(full)
            self._toggle.setVisible(toggle_visible)
            if self._toggle_text:
                self._toggle.setText(self._toggle_text)
            else:
                n_lines = len(full.splitlines())
                self._toggle.setText(f"展开全部（{n_lines} 行 / {len(full)} 字）")

    def _on_toggle(self):
        if self._global_compact:
            self._manual_override = not self._manual_override
            self._expanded = self._manual_override
        else:
            self._expanded = not self._expanded
        self._apply()
        # 展开后重新量高，避免残留空白
        QTimer.singleShot(0, self.refresh_height)
        QTimer.singleShot(30, self.refresh_height)


class _LiveStatusBar(QFrame):
    """实时状态条：显示「正在思考… / 正在调用工具…」等，配旋转动画，避免界面像死机。"""

    _SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        c = theme.colors
        accent = c.get("accent", "#2f6fed")
        self.setStyleSheet("background: transparent;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(6)
        self._dot = QLabel("●")
        self._dot.setStyleSheet(
            f"color: {accent}; font-size: 11px; background: transparent; border: none;"
        )
        lay.addWidget(self._dot)
        self._label = QLabel("")
        self._label.setStyleSheet(
            f"font-size: 12px; color: {c['text_hint']}; "
            "background: transparent; border: none;"
        )
        lay.addWidget(self._label)
        lay.addStretch(1)
        self.setVisible(False)
        self._pulse_on = False
        self._t = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(130)

    def _tick(self):
        if not self._pulse_on:
            return
        self._t += 1
        self._dot.setText(self._SPINNER[self._t % len(self._SPINNER)])

    def set_status(self, text: str):
        self._label.setText(text or "")
        self.setVisible(True)

    def set_pulse(self, on: bool):
        self._pulse_on = bool(on)
        if not on:
            self._dot.setText("●")

    def clear(self):
        self._label.setText("")
        self._pulse_on = False
        self.setVisible(False)


class _ThinkingBlock(QFrame):
    """AI 思考块：可折叠的「💭 思考过程」，默认折叠，借鉴 tokbee 的思路。"""

    def __init__(self, text: str, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        c = theme.colors
        accent = c.get("accent", "#2f6fed")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.setStyleSheet(f"""
            QFrame {{
                background: {c.get("accent_light", "#eaf1fe")};
                border: 1px solid {accent}55;
                border-left: 3px solid {accent};
                border-radius: 8px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(4)
        head = QLabel("💭 思考过程")
        head.setStyleSheet(
            f"font-size: 12px; font-weight: bold; color: {accent}; "
            "background: transparent; border: none;"
        )
        lay.addWidget(head)
        self._body = _ExpandableBody(
            text or "",
            self.theme,
            default_collapsed=True,
            toggle_text="查看思考",
        )
        lay.addWidget(self._body)
        self.bubble = self  # 作为气泡被 _bubbles 追踪


class _ToolStepRow(QFrame):
    """工具步骤行：把一次「工具调用 call + 结果 callback」合并成一行。

    头行默认只显示 工具名 + 状态chip + 折叠箭头；点击展开可见已传参数与返回详情。
    状态在 callback 到达时原位更新：running/pending → ok/empty/failed/skipped。
    """

    STATUS_LABELS = {
        "running": "调用中",
        "pending": "待确认",
        "ok": "成功",
        "empty": "返回为空",
        "failed": "失败",
        "skipped": "未完成",
    }
    STATUS_COLORS = {
        "running": "#f59e0b",
        "pending": "#f59e0b",
        "ok": "#10b981",
        "empty": "#6b7280",
        "failed": "#ef4444",
        "skipped": "#9ca3af",
    }
    _PULSE = ["调用中", "调用中·", "调用中··", "调用中···"]

    def __init__(
        self,
        step_id: str,
        tool: str,
        theme: Theme,
        *,
        args: dict | None = None,
        index: int = 0,
        parent=None,
    ):
        super().__init__(parent)
        self.step_id = step_id
        self.tool = (tool or "tool").strip() or "tool"
        self.theme = theme
        self._index = index
        self._status = "running"
        self._args = args if isinstance(args, dict) else None
        self._callback_display = ""
        self._global_compact = False
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._tick_pulse)
        self._pulse_i = 0
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Maximum)
        self._build()
        self.set_running(args=self._args)

    def _build(self):
        c = self.theme.colors
        self.setStyleSheet(f"""
            QFrame {{
                background: {c.get("tool_bg", "#fff8e1")};
                border: 1px solid {c.get("tool_border", "#f2d97e")};
                border-radius: 10px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(4)
        header = QHBoxLayout()
        header.setSpacing(6)
        label = f"#{self._index} {self.tool}" if self._index else self.tool
        self._name = QLabel(label)
        self._name.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {c['text']}; "
            "background: transparent; border: none;"
        )
        header.addWidget(self._name)
        self._chip = QLabel()
        self._chip.setStyleSheet(
            f"font-size: 11px; padding: 0 8px; border-radius: 8px; "
            f"background: transparent; border: none;"
        )
        header.addWidget(self._chip)
        header.addStretch(1)
        self._toggle = QPushButton("▸")
        self._toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle.setFlat(True)
        self._toggle.setFixedSize(22, 20)
        self._toggle.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; "
            f"color: {c['text_hint']}; font-size: 12px; }}"
        )
        self._toggle.clicked.connect(self._toggle_body)
        header.addWidget(self._toggle)
        lay.addLayout(header)
        self._body = _ExpandableBody(
            "",
            self.theme,
            default_collapsed=True,
            hide_toggle=True,
            toggle_text="查看已传参数与返回",
        )
        lay.addWidget(self._body)

    # ── 状态机 ───────────────────────────────────────────────
    def set_running(self, args: dict | None = None):
        if args is not None and isinstance(args, dict):
            self._args = args
        self._status = "running"
        if not self._pulse_timer.isActive():
            self._pulse_timer.start(500)
        self._apply_header()
        self._apply_body()

    def set_success(self, callback_content: str):
        content = (callback_content or "").strip()
        self._status = "empty" if (not content or "（无输出）" in content) else "ok"
        self._callback_display = content
        self._stop_pulse()
        self._apply_header()
        self._apply_body()

    def set_failed(self, callback_content: str):
        content = (callback_content or "").strip()
        self._status = "failed"
        self._callback_display = content or (
            f"**callback:** `{self.tool}`\n\n```\n（工具抛出的错误未返回正文）\n```"
        )
        self._stop_pulse()
        self._apply_header()
        self._apply_body()

    def set_pending(self):
        self._status = "pending"
        self._stop_pulse()
        self._apply_header()
        self._apply_body()

    def set_skipped(self):
        self._status = "skipped"
        self._stop_pulse()
        self._apply_header()
        self._apply_body()

    def _stop_pulse(self):
        if self._pulse_timer.isActive():
            self._pulse_timer.stop()

    # ── 内部 ───────────────────────────────────────────────
    def _tick_pulse(self):
        if self._status != "running":
            return
        self._pulse_i = (self._pulse_i + 1) % len(self._PULSE)
        self._set_chip(self._PULSE[self._pulse_i], self.STATUS_COLORS["running"])

    def _apply_header(self):
        label = self.STATUS_LABELS.get(self._status, "调用中")
        color = self.STATUS_COLORS.get(self._status, "#f59e0b")
        self._chip.setText(label)
        self._chip.setStyleSheet(
            f"font-size: 11px; padding: 0 8px; border-radius: 8px; "
            f"background: {color}1f; color: {color}; border: none;"
        )

    def _apply_body(self):
        self._body.set_content(self._body_text(), danger=(self._status == "failed"))

    def _set_chip(self, text: str, color: str):
        self._chip.setText(text)
        self._chip.setStyleSheet(
            f"font-size: 11px; padding: 0 8px; border-radius: 8px; "
            f"background: {color}1f; color: {color}; border: none;"
        )

    def _body_text(self) -> str:
        parts = []
        if self._args:
            try:
                from wokbee.core.timeline_format import format_tool_call_for_timeline
                from wokbee.core.credential_store import redact_obj

                parts.append(format_tool_call_for_timeline(self.tool, redact_obj(self._args)))
            except Exception:
                parts.append(f"**call:** `{self.tool}`")
        else:
            parts.append(f"**call:** `{self.tool}`")
        if self._callback_display:
            parts.append(_redact_display(self._callback_display))
        elif self._status in ("running", "pending"):
            parts.append(f"**callback:** `{self.tool}`\n\n（等待返回…）")
        else:
            parts.append(
                f"**callback:** `{self.tool}`\n\n（{self.STATUS_LABELS.get(self._status, '—')}）"
            )
        return "\n\n".join(parts)

    def _toggle_body(self):
        if getattr(self, "_global_compact", False):
            self._body.setVisible(not self._body.isVisible())
            if self._body.isVisible():
                self._body.set_global_compact(True, reset_manual=False)
                self._body._manual_override = True
                self._body._expanded = True
                self._body._apply()
            self._toggle.setText("▾" if self._body.isVisible() else "▸")
        else:
            self._body._on_toggle()
            expanded = getattr(self._body, "_expanded", False)
            self._toggle.setText("▾" if expanded else "▸")
        QTimer.singleShot(0, self._body.refresh_height)
        QTimer.singleShot(30, self._body.refresh_height)

    def set_global_compact(self, compact: bool) -> None:
        """全局收拢：仅保留头行；用户可点 ▸ 展开详情。"""
        self._global_compact = bool(compact)
        if compact:
            self._body.setVisible(False)
            self._body.set_global_compact(True, reset_manual=True)
            self._toggle.setText("▸")
        else:
            self._body.setVisible(True)
            self._body.set_global_compact(False, reset_manual=True)
            expanded = getattr(self._body, "_expanded", False)
            self._toggle.setText("▾" if expanded else "▸")




__all__ = ["_ExpandableBody", "_LiveStatusBar", "_ThinkingBlock", "_ToolStepRow"]

