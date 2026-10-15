"""项目消息路由、允许来源、任务执行和定向通知的业务装配。"""
from __future__ import annotations
import logging
import sys
import threading
from typing import Callable
from types import SimpleNamespace
from PySide6.QtCore import QObject
from beehive.features.wokbee.projects.models import ProjectEvent
from beehive.features.wokbee.projects.project_store import ProjectStore
from beehive.features.wokbee.projects.project_run_queue import project_run_slot
from beehive.features.wokbee.settings import WokBeeSettings
from beehive.system.providers.provider_store import ProviderStore
from beehive.system.gateway.base import Channel, ChannelMessage
from beehive.features.wokbee.gateway.dispatcher import GatewayDispatcher
from beehive.features.wokbee.gateway.router import MessageRouter, RouteOutcome
from beehive.system.gateway.store import GatewayChannelConfig, GatewayStore
from beehive.system.gateway.connection import ConnectionRuntime
from beehive.features.wokbee.gateway.phone_questions import PhoneQuestions
from beehive.features.wokbee.gateway.commands import _run_command
logger = logging.getLogger("wokbee")
_COMMAND_TOKENS = frozenset({"#new", "#list", "#run", "#help"})

class GatewayManager(QObject):
    def __init__(
        self,
        store: GatewayStore | None = None,
        settings: WokBeeSettings | None = None,
        project_store: ProjectStore | None = None,
        provider_store: ProviderStore | None = None,
        dispatcher: GatewayDispatcher | None = None,
        channel_factory: Callable[[GatewayChannelConfig], Channel] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.store = store or GatewayStore()
        self.settings = settings or WokBeeSettings()
        self.project_store = project_store or ProjectStore()
        self.provider_store = provider_store or ProviderStore()
        self.dispatcher = dispatcher or GatewayDispatcher(
            self.settings, self.provider_store, self.project_store
        )
        self.connection = ConnectionRuntime(
            self.store, self._handle, self._on_received,
            lambda msg: self.questions._resolve_pending(msg), channel_factory,
        )
        self.notifier = self.connection.notifier
        self.questions = PhoneQuestions(self._reply, self.notifier)
        self._router = MessageRouter(self.project_store, self.settings)
        if hasattr(self.dispatcher, "event_sink"):
            self.dispatcher.event_sink = self._on_event_written
        if hasattr(self.dispatcher, "ask_sink"):
            self.dispatcher.ask_sink = self.questions._ask_user_sink
        self._project_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def send_automatic_notification(self, text: str) -> tuple[bool, str]:
        """向扫码创建人发送 AutoBee 完成通知。"""
        cfg = self.store.get_config()
        sender_id = cfg.wechat_user_id
        if not sender_id:
            return False, "未记录微信扫码创建人，请重新扫码绑定"
        chan = self.connection._channels.get("wechat")
        if chan is None:
            return False, "微信网关未连接"
        context_token = cfg.wechat_context_token
        if not context_token:
            return False, "等待微信扫码创建人先给机器人发一条消息，以建立通知会话"
        msg = ChannelMessage(
            channel="wechat", sender_id=sender_id, text="", conversation_id=sender_id,
            channel_meta={"context_token": context_token},
        )
        try:
            return chan.send_text(msg, text)
        except Exception as e:
            logger.exception("发送 AutoBee 微信通知失败")
            return False, str(e)

    def _remember_wechat_creator(self, msg: ChannelMessage) -> None:
        """记录扫码创建人的 ID 与会话上下文，供后台任务定向通知。"""
        cfg = self.store.get_config()
        changed = False
        if not cfg.wechat_user_id:
            return
        if cfg.wechat_user_id != msg.sender_id:
            return
        if msg.sender_id and msg.sender_id not in cfg.allow_from:
            cfg.allow_from.append(msg.sender_id)
            changed = True
        context_token = (msg.channel_meta or {}).get("context_token", "")
        if context_token and context_token != cfg.wechat_context_token:
            cfg.wechat_context_token = context_token
            changed = True
        if changed:
            self.store.save_config(cfg)

    def _handle(self, msg: ChannelMessage) -> None:
        # 若该会话正等着一个澄清提问的答复，优先消费这条消息作为答案，不当作新对话路由
        if self.questions._resolve_pending(msg):
            return
        # 优先拦截 IM 管理指令（#new / #list / #run / #help），不路由到项目 Agent
        token, rest = MessageRouter.parse_route_prefix(msg.text or "")
        cmd = (token or "").lower()
        if cmd in _COMMAND_TOKENS:
            # 指令能建项目/跑 Agent，同样受执行白名单约束。
            allow = [s for s in (self.store.get_config().allow_from or []) if s]
            if not allow or msg.sender_id not in allow:
                self._reply(msg, "你暂未获得使用指令的权限：执行白名单仅限指定用户。")
                return
            _run_command(self, cmd, rest, msg)
            return

        cfg = self.store.get_config()
        res = self._router.route(msg, cfg)
        if res.outcome != RouteOutcome.OK:
            if res.reply:
                self._reply(msg, res.reply)
            # 白名单为空或未命中时均拒绝
            if res.outcome == RouteOutcome.DENIED_SENDER:
                self.notifier.log_line.emit(
                    f"[限流] 发送者 {msg.sender_id} 未在允许列表 → 请到「消息网关→允许列表」添加"
                )
            else:
                self.notifier.log_line.emit(f"[{res.outcome}] {msg.sender_id} {res.reply}")
            return

        project_id = res.project_id
        # IM 里用了 @项目ID 显式指定 → 把它设为该频道的默认项目（允许再次 @ 切换，issue 1）
        if res.token:
            cfg = self.store.get_config()
            cfg.set_default_project(msg.channel, project_id)
            self.store.save_config(cfg)
            self.notifier.log_line.emit(
                f"已将 {msg.channel} 的默认项目切换为 {project_id}（后续可再 @项目ID 更换）"
            )
            # 只有 @项目ID、没有正文 → 纯「切换默认项目」：只回执确认，**不跑 Agent**、
            # 也不把空内容交给项目（否则会报「提问内容为空」）。
            if not (res.clean_text or "").strip():
                project = self.project_store.get(project_id)
                name = project.title if project else project_id
                self._reply(msg, (
                    f"✔ 已切换当前频道默认项目：{name}（{project_id}）\n"
                    "之后直接发消息就会路由到该项目；想让它干活可发 #run。"
                ))
                return
        self._run_chat(msg, project_id, res.clean_text)

    def _run_chat(self, msg: ChannelMessage, project_id: str, content: str) -> None:
        """把清理过的用户输入交给默认项目 Agent 跑一遍，并实时落盘事件/回执。"""
        # 与桌面 Agent / AutoBee 共用 FIFO 运行槽；同项目不并发写工作区。
        with project_run_slot(project_id), self._project_lock(project_id):
            self.notifier.log_line.emit(f"开始处理 -> {project_id}: {content[:80]}")
            self._append_event(
                project_id, ProjectEvent(
                    kind="user", content=content,
                    meta={"channel": msg.channel, "sender": msg.sender_id},
                )
            )
            project = self.project_store.get(project_id)
            if project is None:
                self._reply(msg, "项目不存在或已删除。")
                return
            try:
                result = self.dispatcher.run_chat(project, content, context=msg)
            except Exception:
                logger.exception("网关分发失败")
                result = SimpleNamespace(
                    ok=False, outcome="failed", final_text="",
                    error=str(sys.exc_info()[1]),
                )
            reply = self.dispatcher.reply_for(result)
            self._reply(msg, reply)
            self.notifier.message_done.emit(project_id, msg.sender_id, reply[:120])
            self._append_event(
                project_id, ProjectEvent(
                    kind="info",
                    content=f"来自手机（{msg.channel}）的回复已推送。",
                    meta={"sender": msg.sender_id},
                )
            )

    def _reply(self, msg: ChannelMessage, text: str) -> None:
        chan = self.connection._channels.get(msg.channel)
        if chan is None:
            self.notifier.log_line.emit("回执失败：通道未连接")
            return
        try:
            ok, err = chan.send_text(msg, text)
        except Exception:
            logger.exception("发送回执失败")
            ok, err = False, "发送异常"
        if not ok:
            self.notifier.log_line.emit(f"回执失败（{msg.channel}）：{err}")

    def _on_event_written(self, project_id: str, kind: str, content: str, meta: dict) -> None:
        """分发器每条事件落盘后转交（worker 线程）：以 QueuedConnection 回 UI 实时刷新。"""
        self.notifier.event_written.emit(project_id, kind, content, meta)

    def _append_event(self, project_id: str, event: ProjectEvent) -> None:
        try:
            self.project_store.append_event(project_id, event)
        except Exception:
            logger.exception("写入项目事件失败")
        self.notifier.event_written.emit(
            project_id, event.kind, event.content or "", dict(event.meta or {})
        )

    def _project_lock(self, project_id: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._project_locks.get(project_id)
            if lock is None:
                lock = threading.Lock()
                self._project_locks[project_id] = lock
            return lock

    def _on_received(self, msg):
        if msg.channel == "wechat":
            self._remember_wechat_creator(msg)

    def start(self):
        return self.connection.start()

    def sync_channel(self, key):
        return self.connection.sync_channel(key)

    def shutdown(self, wait=False):
        return self.connection.shutdown(wait)

    @property
    def running(self):
        return self.connection.running

    def status_text(self, key):
        return self.connection.status_text(key)

    def is_channel_connected(self, key):
        return self.connection.is_channel_connected(key)
