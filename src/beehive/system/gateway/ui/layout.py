"""通道面板布局装配。"""
from __future__ import annotations
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, QTextEdit, QScrollArea
from beehive.common.ui.styles.inputs import rounded_textedit_qss
from beehive.system.gateway.ui.channels import _ChannelButton
from beehive.system.gateway.ui.styles import _card_frame
def build_panel(self):
    c = self.theme.colors
    outer = QVBoxLayout(self)
    outer.setContentsMargins(0, 0, 24, 0)
    outer.setSpacing(0)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setStyleSheet("QScrollArea { border: none; }")
    body = QWidget()
    body.setStyleSheet(f"background: {c['content_bg']};")
    col = QVBoxLayout(body)
    col.setContentsMargins(28, 24, 28, 24)
    col.setSpacing(14)

    # 标题行
    header = QHBoxLayout()
    header.setSpacing(10)
    htitle = _ChannelButton("__t__", self.header_icon(), self.header_label(), True, self.theme, False)
    htitle.setEnabled(False)
    htitle.setStyleSheet(f"""
            QPushButton {{ color: {c['text']}; font-size: 18px; font-weight: bold;
                background: transparent; border: none; text-align: left; }}
        """)
    header.addWidget(htitle)
    header.addStretch(1)
    col.addLayout(header)

    sub = QLabel(self.header_sub())
    sub.setWordWrap(True)
    sub.setStyleSheet(f"color: {c['text_hint']}; background: transparent; border: none; font-size: 12px;")
    col.addWidget(sub)

    # 接入状态卡（内嵌状态机）
    self._card = self._connect_card()
    col.addWidget(self._card)

    # 运行设置：启用网关（唯一启停开关，勾选即连接、取消即停，不再另设「启动/停止网关」按钮）
    run_card, run = _card_frame(self.theme, "运行设置")
    self._enabled = QCheckBox("启用网关")
    self._enabled.setStyleSheet(f"color: {c['text']}; font-size: 13px; background: transparent; border: none;")
    self._enabled.toggled.connect(self._on_enabled_toggled)
    run.addWidget(self._enabled)
    self._status_lbl = QLabel("未启动")
    self._status_lbl.setStyleSheet(f"color: {c['text_secondary']}; background: transparent; border: none;")
    run.addWidget(self._status_lbl)
    allow_label = QLabel("执行白名单（每行一个 sender ID；扫码创建人自动加入）")
    allow_label.setStyleSheet(f"color: {c['text_secondary']}; background: transparent; border: none;")
    run.addWidget(allow_label)
    self._allow_from = QTextEdit()
    self._allow_from.setPlaceholderText("例如 ou_xxx 或微信 userId")
    self._allow_from.setFixedHeight(58)
    self._allow_from.setStyleSheet(rounded_textedit_qss(c))
    self._allow_from.textChanged.connect(self._on_allow_from_changed)
    run.addWidget(self._allow_from)
    col.addWidget(run_card)

    if self.binding is not None:
        col.addWidget(self.binding.widget)

    col.addStretch(1)
    scroll.setWidget(body)
    outer.addWidget(scroll, stretch=1)

    self._stack = self._card
