"""消息频道导航与接入面板装配。"""
from __future__ import annotations
from functools import partial
from PySide6.QtWidgets import QWidget, QHBoxLayout, QStackedWidget
from beehive.common.ui.theme import Theme
from beehive.system.gateway.connection import ConnectionRuntime
from beehive.system.gateway.ui.channels import _ChannelRail
from beehive.system.gateway.ui.feishu_panel import _FeishuPanel
from beehive.system.gateway.ui.wechat_panel import _WeChatPanel
class GatewayWorkspace(QWidget):
    def __init__(self, theme: Theme, manager: ConnectionRuntime, parent=None, *, binding_factory=None):
        super().__init__(parent)
        self.theme = theme
        self.manager = manager
        self.binding_factory = binding_factory
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
            "feishu": _FeishuPanel(self.theme, self.manager, binding=self.binding_factory("feishu") if self.binding_factory else None),
            "wechat": _WeChatPanel(self.theme, self.manager, binding=self.binding_factory("wechat") if self.binding_factory else None),
        }
        for key, panel in self._panels.items():
            panel.provisioned_changed.connect(partial(self._rail.set_provisioned, key))
            self._stack.addWidget(panel)
        lay.addWidget(self._stack, stretch=1)

    def _show_channel(self, key: str):
        panel = self._panels.get(key)
        if panel is not None:
            self._stack.setCurrentWidget(panel)
            panel.refresh()

    def refresh(self):
        cfg = self.store.get_config()
        for key, panel in self._panels.items():
            self._rail.set_provisioned(key, panel.has_creds(cfg))
        chan = cfg.channel if cfg.channel in self._panels else "feishu"
        self._rail.select(chan)
        self._show_channel(chan)
