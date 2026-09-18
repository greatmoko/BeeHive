"""AI 配置 — 消息网关（UI 参考 dsh-im：左侧频道栏 + 右侧接入面板）。

飞书接入两条路：
- 扫码创建机器人（推荐）：`lark.register_app` 设备流，扫一次码自动建应用并回填凭据。
  二维码来自飞书 `begin` 网络往返（可能几秒），页面先显示「正在连接飞书…」反馈，
  拿到二维码后显示有效倒计时 + 步骤；若 30s 仍无二维码给出超时提示（SDK 的 requests 无超时）。
- 手动接入：粘贴 app_id/app_secret + 测试连接（兜底）。

本文件只做 UI 与线程桥；网关逻辑在 `wokbee.gateway`。
"""

from __future__ import annotations

import json
import threading

from PySide6.QtCore import Qt, QObject, QThread, QTimer, Signal
from PySide6.QtGui import QPixmap, QPainter, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QCheckBox, QComboBox, QTextEdit, QPlainTextEdit, QScrollArea,
    QStackedWidget, QFrame, QProgressBar, QApplication,
)

from tokbee.ui.styles.theme import Theme
from tokbee.ui.styles.system import apply_form_combo
from wokbee.gateway.manager import GatewayManager
from wokbee.gateway.store import GatewayChannelConfig
from wokbee.ui.dialogs import tip as _tip
from wokbee.ui.gateway_channels import _ChannelRail
from wokbee.ui.gateway_channel_panels import _FeishuPanel, _WeChatPanel


class GatewayWorkspace(QWidget):
    def __init__(self, theme: Theme, manager: GatewayManager | None = None, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.manager = manager or GatewayManager()
        self.store = self.manager.store
        self._build()
        self.refresh()

    def _build(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._rail = _ChannelRail(self.theme)
        self._rail.channel_selected.connect(self._show_channel)
        lay.addWidget(self._rail)
        self._stack = QStackedWidget()
        self._panels: dict[str, QWidget] = {
            "feishu": _FeishuPanel(self.theme, self.manager),
            "wechat": _WeChatPanel(self.theme, self.manager),
        }
        for panel in self._panels.values():
            self._stack.addWidget(panel)
        lay.addWidget(self._stack, stretch=1)

    def _show_channel(self, key: str):
        panel = self._panels.get(key)
        if panel is not None:
            self._stack.setCurrentWidget(panel)
            panel.refresh()

    def refresh(self):
        cfg = self.store.get_config()
        chan = cfg.channel if cfg.channel in self._panels else "feishu"
        self._rail.select(chan)
        self._show_channel(chan)


import time as _time


def time_now() -> float:
    return _time.monotonic()


def fmt_duration(seconds: int) -> str:
    seconds = max(0, seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _primary(theme: Theme) -> str:
    c = theme.colors
    return f"""
        QPushButton {{ background: {c['btn_primary']}; color: white; border: none;
            border-radius: 6px; padding: 9px 16px; font-size: 13px; font-weight: bold; }}
        QPushButton:hover {{ background: {c['btn_primary_hover']}; }}
        QPushButton:disabled {{ background: {c['border_light']}; color: {c['text_hint']}; }}
    """


def _secondary(theme: Theme) -> str:
    c = theme.colors
    return f"""
        QPushButton {{ background: transparent; color: {c['text']};
            border: 1px solid {c['border']}; border-radius: 6px; padding: 8px 14px; font-size: 13px; }}
        QPushButton:hover {{ background: {c['card_hover']}; }}
    """


def _input(theme: Theme) -> str:
    c = theme.colors
    return f"""
        QLineEdit {{ background: {c['input_bg']}; color: {c['text']};
            border: 1px solid {c['input_border']}; border-radius: 6px;
            padding: 6px 8px; font-size: 13px; }}
        QLineEdit:focus {{ border: 1px solid {c['input_focus_border']}; }}
    """


def _textarea(theme: Theme) -> str:
    c = theme.colors
    return f"""
        QPlainTextEdit, QTextEdit {{ background: {c['input_bg']}; color: {c['text']};
            border: 1px solid {c['input_border']}; border-radius: 6px;
            padding: 6px 8px; font-size: 13px; }}
        QPlainTextEdit:focus, QTextEdit:focus {{ border: 1px solid {c['input_focus_border']}; }}
    """


def _progressbar(theme: Theme) -> str:
    c = theme.colors
    return f"""
        QProgressBar {{ background: {c['border_light']}; border: none; border-radius: 4px; }}
        QProgressBar::chunk {{ background: {c['accent']}; border-radius: 4px; }}
    """
