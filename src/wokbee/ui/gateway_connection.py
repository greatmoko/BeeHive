"""网关 UI 后台连接与扫码 worker."""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QThread, Signal

from wokbee.gateway.store import GatewayChannelConfig
class _ProvisionWorker(QThread):
    qr_ready = Signal(str, int)      # url, expire_in
    status_change = Signal(str)
    done = Signal(str, str, object)  # app_id, app_secret, user_info
    failed = Signal(str)

    def __init__(self, name: str = "WokBee", parent=None):
        super().__init__(parent)
        self._name = name
        self._provisioner = None

    def run(self):
        from wokbee.gateway.provision import FeishuProvisioner

        def on_qr(info):
            self.qr_ready.emit(info.get("url", ""), int(info.get("expire_in", 0) or 0))

        self._provisioner = FeishuProvisioner(
            on_qr_code=on_qr,
            on_status_change=lambda info: self.status_change.emit(info.get("status", "")),
            name=self._name,
        )
        try:
            result = self._provisioner.run()
            self.done.emit(
                result.get("client_id", ""),
                result.get("client_secret", ""),
                result.get("user_info") or {},
            )
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))

    def cancel(self):
        if self._provisioner:
            self._provisioner.cancel()


class _WeChatProvisionWorker(QObject):
    """微信扫码登录 worker。

    `weixin_ilink.login` 同步阻塞最长 ~8 分钟、**无 cancel**；故用 **daemon thread**（而非
    QThread）跑，窗口关闭进程直接结束，不会因该线程阻塞退出。取消仅设标志丢弃结果。
    """

    qr_ready = Signal(str)         # 二维码 URL（用 _render_qr 渲染）
    status_change = Signal(str)
    done = Signal(dict)            # info_json {botToken,accountId,baseUrl,userId}
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._prov = None
        self._thread = None

    def start(self):
        from wokbee.gateway.provision_wechat import WeChatProvisioner

        self._prov = WeChatProvisioner(
            on_qrcode=lambda url: self.qr_ready.emit(url),
            on_status_change=lambda s: self.status_change.emit(s),
        )
        self._thread = threading.Thread(target=self._run, name="wechat-login", daemon=True)
        self._thread.start()

    def _run(self):
        try:
            info = self._prov.run()
            if info:
                self.done.emit(info)
        except InterruptedError:
            pass  # 用户取消：仅忽略，线程 daemon 自行结束
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))

    def cancel(self):
        if self._prov:
            self._prov.cancel()


class _GatewayTestWorker(QThread):
    result = Signal(str)

    def __init__(self, store, cfg: GatewayChannelConfig, parent=None):
        super().__init__(parent)
        self._store = store
        self._cfg = cfg

    def run(self):
        try:
            ok, msg = self._store.test_connection(self._cfg)
        except Exception as e:  # noqa: BLE001
            ok, msg = False, str(e)
        self.result.emit(("连接成功。" if ok else "连接失败：") + str(msg))


# ── 左侧频道栏 ──────────────────────────────────────

__all__ = ["_ProvisionWorker", "_WeChatProvisionWorker", "_GatewayTestWorker"]

