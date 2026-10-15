"""微信扫码与凭据差异面板。"""
from __future__ import annotations
from beehive.system.gateway.store import GatewayChannelConfig
from beehive.common.ui.dialogs.message import show_tip as _tip
from beehive.system.gateway.ui.workers import _WeChatProvisionWorker
from beehive.system.gateway.ui.panels import _ChannelPanelBase
class _WeChatPanel(_ChannelPanelBase):
    def channel_key(self) -> str:
        return "wechat"

    def channel_display(self) -> str:
        return "微信"

    def header_icon(self) -> str:
        return "💬"

    def header_label(self) -> str:
        return "微信机器人"

    def header_sub(self) -> str:
        return ("扫一次码，就能在微信里使用 WokBee：手机聊天即可遥控本机项目 Agent（收到文本 → 路由 → 运行 → 回复）。")

    def idle_headline(self) -> str:
        return "扫一次码，就能在微信里使用"

    def idle_desc(self) -> str:
        return ("用手机微信扫描二维码并确认。绑定凭据会直接写入本机，浏览器不会收到 token。\n"
                "注意：这是个人微信账号接入，仅支持私聊；请勿用于营销/群发。")

    def idle_btn_text(self) -> str:
        return "生成微信二维码"

    def qr_connecting_text(self) -> str:
        return "正在连接微信服务，生成二维码…"

    def qr_ready_text(self) -> str:
        return "● 请用手机微信「扫一扫」读取二维码，并确认登录"

    def qr_ready_steps(self) -> str:
        return ("1. 打开手机微信，使用「扫一扫」读取二维码\n"
                "2. 在手机上确认登录\n"
                "3. 保持本页打开，等待绑定就绪")

    def status_map(self) -> dict[str, str]:
        return {
            "waiting": "等待手机微信扫描确认…",
            "scanned": "已扫描，请在手机上确认登录…",
            "refreshing": "二维码已刷新，请重新扫描…",
            "redirected": "正在切换服务节点…",
        }

    def uses_countdown(self) -> bool:
        return False

    def watchdog_ms(self) -> int:
        return 60000

    def has_creds(self, cfg: GatewayChannelConfig) -> bool:
        return bool(cfg.wechat_bot_token and cfg.wechat_account_id and cfg.wechat_base_url)

    def creds_summary(self, cfg: GatewayChannelConfig) -> str:
        return f"账号：{cfg.wechat_account_id}"

    def make_scan_worker(self):
        return _WeChatProvisionWorker(parent=self)



    def finish_provision(self, result: tuple) -> None:
        info = result[0]
        cfg = self.store.get_config()
        cfg.channel = "wechat"
        cfg.wechat_bot_token = str(info.get("botToken", "") or "")
        cfg.wechat_account_id = str(info.get("accountId", "") or "")
        cfg.wechat_base_url = str(info.get("baseUrl", "") or "")
        cfg.wechat_user_id = str(info.get("userId", "") or "")
        if cfg.wechat_user_id and cfg.wechat_user_id not in cfg.allow_from:
            cfg.allow_from.append(cfg.wechat_user_id)
        cfg.set_channel_enabled("wechat", True)  # 扫码成功即自动启用本频道（不动飞书）
        self.store.save_config(cfg)
        self._syncing = True
        try:
            self._enabled.setChecked(True)
        finally:
            self._syncing = False
        self._conn_app.setText(f"账号：{cfg.wechat_account_id}")
        self._states.setCurrentWidget(self._connected_page)
        self.manager.start()  # 用刚保存的凭据立即建长轮询，无需用户再点启动
        self._refresh_status()
        _tip(self, self.theme,
             "微信已绑定并保存凭据，正在建立连接。之后手机给机器人发消息即可遥控本机 Agent；"
             "重启应用会自动重连，会话过期时需重新扫码。")
