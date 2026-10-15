"""飞书扫码与凭据差异面板。"""
from __future__ import annotations
from beehive.system.gateway.store import GatewayChannelConfig
from beehive.common.ui.dialogs.message import show_tip as _tip
from beehive.system.gateway.ui.workers import _ProvisionWorker
from beehive.system.gateway.ui.panels import _ChannelPanelBase
class _FeishuPanel(_ChannelPanelBase):
    def channel_key(self) -> str:
        return "feishu"

    def channel_display(self) -> str:
        return "飞书"

    def header_icon(self) -> str:
        return "✈️"

    def header_label(self) -> str:
        return "飞书机器人"

    def header_sub(self) -> str:
        return ("扫一次码，就能在飞书里使用 WokBee：手机聊天即可遥控本机项目 Agent（收到文本 → 路由 → 运行 → 回复）。")

    def idle_headline(self) -> str:
        return "扫一次码，就能在飞书里使用"

    def idle_desc(self) -> str:
        return ("二维码由飞书服务签发。用手机飞书扫描并确认后，账号凭据会直接写入本机，"
                "浏览器不会收到 app_secret。")

    def idle_btn_text(self) -> str:
        return "生成飞书二维码"

    def qr_connecting_text(self) -> str:
        return "正在连接飞书服务，生成二维码…"

    def qr_ready_text(self) -> str:
        return "● 正在添加新机器人 — 请用手机飞书扫码并确认"

    def qr_ready_steps(self) -> str:
        return ("1. 打开飞书移动端，使用「扫一扫」读取二维码\n"
                "2. 核对应用名称与权限范围，确认创建\n"
                "3. 保持本页打开，等待新机器人的长连接就绪")

    def status_map(self) -> dict[str, str]:
        return {
            "polling": "已拉起扫码页，等待手机飞书确认…",
            "slow_down": "操作太频繁，请稍后再试。",
            "domain_switched": "正在切换服务节点…",
        }

    def uses_countdown(self) -> bool:
        return True

    def watchdog_ms(self) -> int:
        return 30000

    def has_creds(self, cfg: GatewayChannelConfig) -> bool:
        return bool(cfg.feishu_app_id and cfg.feishu_app_secret)

    def creds_summary(self, cfg: GatewayChannelConfig) -> str:
        return f"应用标识：{cfg.feishu_app_id}"

    def make_scan_worker(self):
        return _ProvisionWorker(name="WokBee", parent=self)



    def finish_provision(self, result: tuple) -> None:
        app_id, app_secret = result[0], result[1]
        cfg = self.store.get_config()
        user_info = result[2] if len(result) > 2 and isinstance(result[2], dict) else {}
        creator_id = str(
            user_info.get("open_id") or user_info.get("openId")
            or user_info.get("user_id") or user_info.get("userId") or ""
        ).strip()
        cfg.feishu_app_id = app_id
        cfg.feishu_app_secret = app_secret
        if creator_id and creator_id not in cfg.allow_from:
            cfg.allow_from.append(creator_id)
        cfg.set_channel_enabled("feishu", True)  # 扫码成功即自动启用本频道（不动微信）
        cfg.channel = "feishu"
        self.store.save_config(cfg)
        self._syncing = True
        try:
            self._enabled.setChecked(True)
        finally:
            self._syncing = False
        self._conn_app.setText(f"应用标识：{app_id}")
        self._states.setCurrentWidget(self._connected_page)
        self.manager.start()  # 用刚保存的凭据立即建长连接，无需用户再点启动
        self._refresh_status()
        _tip(self, self.theme,
             "飞书机器人已创建并保存凭据，正在建立长连接。之后手机给机器人发消息即可遥控本机 Agent；"
             "重启应用会自动重连，无需再次扫码。")
