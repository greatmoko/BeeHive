"""WokBee 消息网关：把手机 IM（飞书首发）消息路由到本地项目 Agent。

本包只做网关自身；引擎（deepagents 等）在 `beehive.features.wokbee.execution`，由 `GatewayDispatcher`
在后台线程按需加载（与 `beehive.features.autobee.engine.executor` 相同契约，绝不在 UI 线程 import）。
"""

from __future__ import annotations

from beehive.system.gateway.base import Channel, ChannelMessage, ChannelStatus
from beehive.system.gateway.store import GatewayChannelConfig, GatewayStore
from beehive.features.wokbee.gateway.router import MessageRouter, RouteOutcome, RouteResult
from beehive.features.wokbee.gateway.dispatcher import GatewayDispatcher
from beehive.system.gateway.feishu import FeishuChannel
from beehive.system.gateway.provision import FeishuProvisioner
from beehive.features.wokbee.gateway.manager import GatewayManager
from beehive.system.gateway.connection import GatewayNotifier

__all__ = [
    "Channel",
    "ChannelMessage",
    "ChannelStatus",
    "GatewayChannelConfig",
    "GatewayStore",
    "MessageRouter",
    "RouteOutcome",
    "RouteResult",
    "GatewayDispatcher",
    "FeishuChannel",
    "FeishuProvisioner",
    "GatewayManager",
    "GatewayNotifier",
]
