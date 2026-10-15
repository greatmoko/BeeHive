"""多频道协议连接、收件队列与分发线程生命周期。"""
from __future__ import annotations
import logging
import queue
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Callable
from PySide6.QtCore import QObject, Signal
from beehive.system.gateway.base import Channel, ChannelMessage, ChannelStatus
from beehive.system.gateway.store import GatewayChannelConfig
logger = logging.getLogger("wokbee")

_SENTINEL = object()  # 队列停止哨兵

_CHANNEL_STATUS_TEXT = {
    ChannelStatus.IDLE: "已就绪",
    ChannelStatus.CONNECTING: "连接{chan}中…",
    ChannelStatus.CONNECTED: "已连接（等待{chan}推送）",
    ChannelStatus.ERROR: "{chan}连接失败（检查网络与凭据；若微信提示过期请重新扫码）",
    ChannelStatus.STOPPED: "已停止",
}

_CHANNEL_DISPLAY = {"feishu": "飞书", "wechat": "微信"}

_MESSAGE_DEDUP_TTL = 3600.0

_MESSAGE_DEDUP_MAX = 4096

class GatewayNotifier(QObject):
    """网关 → UI 的 Qt 信号桥（跨线程自动 QueuedConnection）。"""

    status_changed = Signal(str, str)       # channel_key, 人类可读状态
    log_line = Signal(str)                  # 实时日志
    message_done = Signal(str, str, str)    # project_id, sender_id, reply-brief
    event_written = Signal(str, str, str, object)  # project_id, kind, content, meta（实时流）
    error = Signal(str, str)                # channel_key, 严重错误提示

def feishu_factory(cfg: GatewayChannelConfig) -> Channel:
    from beehive.system.gateway.feishu import FeishuChannel
    return FeishuChannel(cfg)

def wechat_factory(cfg: GatewayChannelConfig) -> Channel:
    from beehive.system.gateway.wechat import WeChatChannel
    return WeChatChannel(cfg)

_CHANNEL_FACTORIES = {
    "feishu": feishu_factory,
    "wechat": wechat_factory,
}
class ConnectionRuntime:
    """连接、队列和有界去重；业务处理通过回调注入。"""
    def __init__(self, store, handle_message, on_received, consume_incoming, channel_factory=None):
        self.store = store
        self.notifier = GatewayNotifier()
        self._handle = handle_message
        self._on_received = on_received
        self._consume_incoming = consume_incoming
        self._channel_factory = channel_factory
        self._channels = {}
        self._inbox = queue.Queue()
        self._pool = ThreadPoolExecutor(max_workers=2)
        self._dispatcher_thread = None
        self._in_flight = OrderedDict()
        self._in_flight_guard = threading.Lock()

    def _factory_for(self, key: str) -> Callable[[GatewayChannelConfig], Channel]:
        if self._channel_factory is not None:
            return self._channel_factory
        return _CHANNEL_FACTORIES.get(key, feishu_factory)

    @staticmethod
    def _creds_present(key: str, cfg: GatewayChannelConfig) -> bool:
        """某频道的连接凭据是否齐全（决定能否实际建立长连接）。"""
        if key == "wechat":
            return bool(cfg.wechat_bot_token and cfg.wechat_account_id and cfg.wechat_base_url)
        return bool(cfg.feishu_app_id and cfg.feishu_app_secret)

    def start(self) -> None:
        """启动所有已启用且已有凭据的频道（幂等；Feishu / WeChat 可同时在线）。"""
        cfg = self.store.get_config()
        for key in _CHANNEL_FACTORIES:
            self.sync_channel(key)
        if not any(cfg.channel_enabled(k) for k in _CHANNEL_FACTORIES):
            self.notifier.log_line.emit("网关未启用任何频道——可在「消息网关」勾选「启用网关」。")

    def sync_channel(self, key: str) -> None:
        """按当前配置把某频道拉到期望状态：启用+有凭据则连接，否则停止。

        面板勾选/取消「启用网关」与启动时统一走这里，保证「勾选即连、取消即停」，且
        只影响本频道、不动其它频道（issue：不同 IM 互不干扰）。
        """
        cfg = self.store.get_config()
        disp = _CHANNEL_DISPLAY.get(key, key)
        if not cfg.channel_enabled(key):
            self._stop_channel(key)
            return
        if not self._creds_present(key, cfg):
            self._stop_channel(key)
            self.notifier.log_line.emit(
                f"「{disp}」已启用但未配置凭据——请扫码创建/登录，或填写凭据后进入再勾选启用。"
            )
            return
        self._ensure_channel(key, cfg)

    def _ensure_channel(self, key: str, cfg: GatewayChannelConfig) -> None:
        """建一条频道长连接（若已存在则忽略）。"""
        if key in self._channels:
            return
        chan = self._factory_for(key)(cfg)
        self._channels[key] = chan
        chan.on_message(self._on_incoming)
        chan.set_status_cb(lambda status, _k=key: self._on_channel_status(_k, status))
        disp = _CHANNEL_DISPLAY.get(key, key)
        self.notifier.log_line.emit(f"启动{disp}长连接（等待{disp}推流）…")
        try:
            chan.start()
        except Exception:
            logger.exception("启动通道失败")
            self._channels.pop(key, None)
            self.notifier.error.emit(key, f"启动{disp}长连接失败，请检查配置并查看日志。")
            return
        self._ensure_dispatch_loop()

    def _ensure_dispatch_loop(self) -> None:
        if self._dispatcher_thread is None or not self._dispatcher_thread.is_alive():
            self._dispatcher_thread = threading.Thread(
                target=self._dispatch_loop, name="gw-dispatch", daemon=True
            )
            self._dispatcher_thread.start()

    def _stop_channel(self, key: str) -> None:
        chan = self._channels.pop(key, None)
        if chan is None:
            return
        try:
            chan.stop()
        except Exception:
            logger.exception("停止通道失败")
        self.notifier.status_changed.emit(key, self._status_text(ChannelStatus.STOPPED, key))

    def shutdown(self, wait: bool = False) -> None:
        for key in list(self._channels):
            self._stop_channel(key)
        self._inbox.put(_SENTINEL)
        if (
            self._dispatcher_thread is not None
            and self._dispatcher_thread is not threading.current_thread()
        ):
            self._dispatcher_thread.join(timeout=None if wait else 3.0)
            self._dispatcher_thread = None
        self._pool.shutdown(wait=wait, cancel_futures=True)

    @property
    def running(self) -> bool:
        return bool(self._channels)

    def _on_channel_status(self, key: str, status: ChannelStatus) -> None:
        self.notifier.status_changed.emit(key, self._status_text(status, key))

    def _status_text(self, status: ChannelStatus, key: str) -> str:
        """把某频道的状态转成人类可读文案；失败时附加 `_last_err` 具体原因。"""
        chan_name = _CHANNEL_DISPLAY.get(key, key)
        text = _CHANNEL_STATUS_TEXT.get(status, status.value)
        try:
            text = text.format(chan=chan_name)
        except Exception:  # noqa: BLE001
            pass
        if status == ChannelStatus.ERROR:
            chan = self._channels.get(key)
            err = getattr(chan, "_last_err", "")
            if err:
                text = f"{chan_name}连接失败：{err}"
        return text

    def status_text(self, key: str) -> str:
        """某频道当前状态的文本（面板按自己频道读取，不串台）。"""
        chan = self._channels.get(key)
        if chan is not None:
            return self._status_text(chan.status, key)
        return "未启动"

    def is_channel_connected(self, key: str) -> bool:
        """返回频道当前是否已建立连接。"""
        chan = self._channels.get(key)
        return chan is not None and chan.status == ChannelStatus.CONNECTED

    def _claim_message_id(self, message_id: str) -> bool:
        now = time.monotonic()
        with self._in_flight_guard:
            while self._in_flight:
                oldest_id, seen_at = next(iter(self._in_flight.items()))
                if now - seen_at <= _MESSAGE_DEDUP_TTL:
                    break
                self._in_flight.pop(oldest_id, None)
            if message_id in self._in_flight:
                self._in_flight.move_to_end(message_id)
                return False
            self._in_flight[message_id] = now
            while len(self._in_flight) > _MESSAGE_DEDUP_MAX:
                self._in_flight.popitem(last=False)
            return True

    def _dispatch_loop(self) -> None:
        while True:
            msg = self._inbox.get()
            if msg is _SENTINEL:
                break
            self._pool.submit(self._handle, msg)
    def _on_incoming(self, msg: ChannelMessage) -> None:
        """通道收线程回调：只去重 + 入队，快速返回。"""
        self.notifier.log_line.emit(
            f"收到 {msg.channel} 消息（{msg.sender_id}）：{(msg.text or '')[:80]}"
        )
        self._on_received(msg)
        mid = msg.message_id
        if mid and not self._claim_message_id(mid):
            return
        # 该会话正等着澄清答复：直接在收线程解除阻塞（快），不再入队当新对话处理
        if self._consume_incoming(msg):
            return
        self._inbox.put(msg)
