"""网关 UI 频道选择栏."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QVBoxLayout, QLabel, QPushButton, QWidget

from tokbee.ui.styles.theme import Theme
class _ChannelButton(QPushButton):
    activated = Signal(str)

    CHANNELS = [
        ("wechat", "💬", "微信", True),
        ("feishu", "✈️", "飞书", True),
    ]

    def __init__(self, key: str, icon: str, label: str, enabled: bool, theme: Theme, active: bool):
        super().__init__()
        self._key = key
        self._theme = theme
        self._available = enabled
        self.setText(f"{icon}  {label}" + ("" if enabled else "  ·即将上线"))
        self.setCursor(Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCheckable(True)
        self.toggled.connect(self._apply)  # setChecked(active) 之后也要重绘高亮
        self.setChecked(active)
        self.clicked.connect(lambda: self.activated.emit(self._key))

    def _apply(self):
        c = self._theme.colors
        if not self._available:
            qss = f"""
                QPushButton {{ color: {c['text_hint']}; background: transparent;
                    border: none; text-align: left; padding: 10px 14px; }}
            """
        elif self.isChecked():
            qss = f"""
                QPushButton {{ color: {c['text']}; background: {c['card_bg']};
                    border: 1px solid {c['accent']}; border-radius: 8px;
                    text-align: left; padding: 10px 14px; font-weight: bold; }}
            """
        else:
            qss = f"""
                QPushButton {{ color: {c['subnav_text']}; background: transparent;
                    border: none; text-align: left; padding: 10px 14px; }}
                QPushButton:hover {{ color: {c['text']}; background: {c['sidebar_hover']}; }}
            """
        self.setStyleSheet(qss)


class _ChannelRail(QWidget):
    channel_selected = Signal(str)

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._buttons: dict[str, _ChannelButton] = {}
        self._build()

    def _build(self):
        c = self.theme.colors
        self.setFixedWidth(206)
        self.setStyleSheet(f"background: {c['subnav_bg']};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 18, 14, 18)
        lay.setSpacing(8)

        title = _ChannelButton("__logo__", "📡", "消息网关", True, self.theme, False)
        title.setEnabled(False)
        title.setStyleSheet(f"""
            QPushButton {{ color: {c['text']}; font-size: 15px; font-weight: bold;
                background: transparent; border: none; text-align: left; padding: 2px 2px 10px 2px; }}
        """)
        lay.addWidget(title)

        for key, icon, label, enabled in _ChannelButton.CHANNELS:
            active = key == "feishu"
            btn = _ChannelButton(key, icon, label, enabled, self.theme, active)
            if enabled:
                btn.activated.connect(self._on_activated)
            self._buttons[key] = btn
            lay.addWidget(btn)

        lay.addStretch(1)
        note = QLabel("当前支持微信与飞书。")
        note.setWordWrap(True)
        note.setStyleSheet(
            f"color: {c['text_hint']}; background: transparent; border: none; font-size: 11px;"
        )
        lay.addWidget(note)

    def _on_activated(self, key: str):
        for btn in self._buttons.values():
            btn.setChecked(btn._key == key and btn._available)
        self.channel_selected.emit(key)

    def select(self, key: str):
        """程序化选中某个已启用频道（供 refresh() 按配置记忆）。"""
        if key in self._buttons and self._buttons[key]._available:
            self._on_activated(key)


# ── 接入面板基类（飞书 / 微信共用，两面板近乎一致故收敛为一份） ────────────

__all__ = ["_ChannelButton", "_ChannelRail"]

