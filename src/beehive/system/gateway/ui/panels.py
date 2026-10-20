"""通道状态与配置界面；业务绑定卡由应用传入。"""
from __future__ import annotations
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QApplication
from beehive.common.ui.theme import Theme
from beehive.system.gateway.connection import ConnectionRuntime
from beehive.system.gateway.store import GatewayChannelConfig
from beehive.common.ui.dialogs.message import show_tip as _tip
from beehive.system.gateway.ui.provisioning import ProvisionController
from beehive.system.gateway.ui.layout import build_panel
from beehive.system.gateway.ui.connection_card import build_connection_card

class _ChannelPanelBase(QWidget):
    provisioned_changed = Signal(bool)

    def __init__(self, theme: Theme, manager: ConnectionRuntime, parent=None, *, binding=None):
        super().__init__(parent)
        self.theme = theme
        self.manager = manager
        self.store = manager.store
        self.binding = binding
        self._syncing = False
        self.provision = ProvisionController(self)
        self._build()
        self._connect()

    def channel_key(self) -> str:
        raise NotImplementedError

    def channel_display(self) -> str:
        raise NotImplementedError

    def header_icon(self) -> str:
        raise NotImplementedError

    def header_label(self) -> str:
        raise NotImplementedError

    def header_sub(self) -> str:
        raise NotImplementedError

    def idle_headline(self) -> str:
        raise NotImplementedError

    def idle_desc(self) -> str:
        raise NotImplementedError

    def idle_btn_text(self) -> str:
        raise NotImplementedError

    def qr_connecting_text(self) -> str:
        raise NotImplementedError

    def qr_ready_text(self) -> str:
        raise NotImplementedError

    def qr_ready_steps(self) -> str:
        raise NotImplementedError

    def status_map(self) -> dict[str, str]:
        return {}

    def uses_countdown(self) -> bool:
        return False

    def watchdog_ms(self) -> int:
        return 30000

    def has_creds(self, cfg: GatewayChannelConfig) -> bool:
        raise NotImplementedError

    def creds_summary(self, cfg: GatewayChannelConfig) -> str:
        return ""

    def make_scan_worker(self):
        raise NotImplementedError

    def finish_provision(self, result: tuple) -> None:
        raise NotImplementedError

    def _build(self):
        return build_panel(self)

    def _connect_card(self):
        return build_connection_card(self)

    def _connect(self):
        # 状态/错误按 channel_key 分流：飞书面板只看飞书状态、微信面板只看微信状态（不串台）
        self.manager.notifier.status_changed.connect(self._on_status)
        self.manager.notifier.error.connect(self._on_error)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.provision.cancel_provision)

    def _on_status(self, key: str, text: str):
        if key != self.channel_key():
            return
        self._status_lbl.setText(text)
        self._conn_status.setText(text)

    def _on_error(self, key: str, msg: str):
        if key != self.channel_key():
            return
        _tip(self, self.theme, msg)

    def _on_enabled_toggled(self, checked: bool):
        """「启用网关」勾选 = 该频道唯一启停开关：勾选 → 保存并连接；取消 → 停止。

        只启停**本频道**，不影响其它频道（issue：不同 IM 可同时在线、互不干扰）。
        用 `_syncing` 抑制程序化 setChecked（refresh / finish_provision）触发的重复启停。
        """
        if self._syncing:
            return
        key = self.channel_key()
        cfg = self.store.get_config()
        cfg.set_channel_enabled(key, bool(checked))
        cfg.channel = key  # 记住主显示频道（工作区默认显示用；不再决定其它频道能否跑）
        self.store.save_config(cfg)
        self.manager.sync_channel(key)
        self._refresh_status()

    def _on_allow_from_changed(self):
        if self._syncing:
            return
        text = self._allow_from.toPlainText()
        ids = []
        for raw in text.replace(",", "\n").splitlines():
            value = raw.strip()
            if value and value not in ids:
                ids.append(value)
        cfg = self.store.get_config()
        cfg.allow_from = ids
        self.store.save_config(cfg)

    def _refresh_status(self):
        st = self.manager.status_text(self.channel_key())
        self._status_lbl.setText(st)
        self._conn_status.setText(st)
        self.provisioned_changed.emit(self.has_creds(self.store.get_config()))

    def refresh(self):
        cfg = self.store.get_config()
        self._syncing = True
        try:
            self._enabled.setChecked(cfg.channel_enabled(self.channel_key()))
            self._allow_from.setPlainText("\n".join(cfg.allow_from))
            if self.binding is not None:
                self.binding.refresh()
        finally:
            self._syncing = False
        if self.has_creds(cfg):
            self._conn_app.setText(self.creds_summary(cfg))
            self._states.setCurrentWidget(self._connected_page)
        else:
            self._states.setCurrentWidget(self._idle_page)
        self._refresh_status()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
