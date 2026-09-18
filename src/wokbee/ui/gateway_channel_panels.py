"""网关频道接入面板：连接卡片、启停配置和频道差异钩子."""

from __future__ import annotations

import time as _time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap, QPainter, QColor
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QCheckBox, QComboBox, QTextEdit, QPlainTextEdit, QScrollArea, QStackedWidget, QFrame, QProgressBar, QApplication

from tokbee.ui.styles.theme import Theme
from tokbee.ui.styles.system import apply_form_combo
from wokbee.gateway.manager import GatewayManager
from wokbee.gateway.store import GatewayChannelConfig
from wokbee.ui.dialogs import tip as _tip
from wokbee.ui.gateway_channels import _ChannelButton
from wokbee.ui.gateway_connection import _ProvisionWorker, _WeChatProvisionWorker, _GatewayTestWorker
def _render_qr(url: str, size: int = 260) -> QPixmap | None:
    """把字符串画成二维码位图（用 qrcode 纯 python 矩阵，不依赖 Pillow）。

    `get_matrix()` 已含 quiet zone（border），故整幅直接画；长授权 URL 会自动升版本。
    """
    try:
        import qrcode
        qr = qrcode.QRCode(border=2, box_size=1)
        qr.add_data(url)
        qr.make(fit=True)
        matrix = qr.get_matrix()
    except Exception:
        return None
    n = len(matrix)
    scale = max(1, size // n)
    pix = QPixmap(n * scale, n * scale)
    pix.fill(QColor("white"))
    painter = QPainter(pix)
    painter.setBrush(QColor("black"))
    for r, row in enumerate(matrix):
        for c, cell in enumerate(row):
            if cell:
                painter.drawRect(c * scale, r * scale, scale, scale)
    painter.end()
    return pix


# ── 接入面板基类 ──────────────────────────────────────
class _ChannelPanelBase(QWidget):
    """消息接入面板：接入状态卡（扫码三步） + 运行卡（启用/启停） + 项目绑定。

    频道差异由子类钩子提供：
      channel_key() / channel_display() / header_icon() / header_label() / header_sub()
      idle_headline() / idle_desc() / idle_btn_text() / qr_connecting_text()
      qr_ready_text() / qr_ready_steps() / status_map() / uses_countdown() / watchdog_ms()
      has_creds(cfg) / creds_summary(cfg) / make_scan_worker() / finish_provision(result)
    """

    def __init__(self, theme: Theme, manager: GatewayManager, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.manager = manager
        self.store = manager.store
        self._scan_worker = None
        self._syncing = False  # 程序化 setChecked/setCurrentIndex 时抑制启停与保存回调
        self._watchdog: QTimer | None = None
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._tick)
        self._deadline = 0.0
        self._remaining = 0
        self._build()
        self._connect()

    # ── 子类钩子 ─────────────────────────────
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

    def _read_default_project(self, cfg: GatewayChannelConfig) -> str:
        """读取本频道的默认项目 id（不同频道各绑各的，issue 3）。"""
        raise NotImplementedError

    def _write_default_project(self, cfg: GatewayChannelConfig, project_id: str) -> None:
        """把本频道的默认项目 id 写回配置。"""
        raise NotImplementedError

    # ── 布局 ─────────────────────────────
    def _build(self):
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
        self._allow_from = QPlainTextEdit()
        self._allow_from.setPlaceholderText("例如 ou_xxx 或微信 userId")
        self._allow_from.setFixedHeight(58)
        self._allow_from.setStyleSheet(_textarea(self.theme))
        self._allow_from.textChanged.connect(self._on_allow_from_changed)
        run.addWidget(self._allow_from)
        col.addWidget(run_card)

        # 项目绑定：默认项目（无前缀消息落点）
        route_card, route = _card_frame(self.theme, "项目绑定")
        prj_hint = QLabel("无前缀消息路由到默认项目；可用 @项目ID 切换指定项目；#help 查看全部指令。")
        prj_hint.setWordWrap(True)
        prj_hint.setStyleSheet(f"color: {c['text_hint']}; background: transparent; border: none; font-size: 12px;")
        route.addWidget(prj_hint)
        self._default_project = QComboBox()
        apply_form_combo(self._default_project, self.theme.colors)
        self._default_project.currentIndexChanged.connect(self._on_default_project_changed)
        route.addWidget(self._default_project)
        col.addWidget(route_card)

        col.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, stretch=1)

        self._stack = self._card

    # ── 接入状态卡 ────────────────────────────────
    def _connect_card(self) -> QWidget:
        c = self.theme.colors
        frame = QFrame()
        frame.setObjectName("gwCard")
        frame.setStyleSheet(
            f"QFrame#gwCard {{ background: {c['card_bg']}; border: 1px solid {c['border_light']}; "
            f"border-radius: 10px; }}"
        )
        root = QVBoxLayout(frame)
        root.setContentsMargins(24, 22, 24, 22)

        stack = QStackedWidget()
        stack.setStyleSheet("background: transparent;")

        # page 0 : idle（尚无凭据 → 扫码引导）
        idle = QWidget()
        idle.setStyleSheet("background: transparent;")
        il = QVBoxLayout(idle)
        il.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        il.setSpacing(12)
        icon = QLabel(self.header_icon())
        icon.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        icon.setStyleSheet(f"font-size: 46px; background: transparent; border: none;")
        il.addWidget(icon)
        h1 = QLabel(self.idle_headline())
        h1.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        h1.setStyleSheet(f"font-size: 22px; font-weight: bold; color: {c['text']}; background: transparent; border: none;")
        il.addWidget(h1)
        h2 = QLabel("WokBee")
        h2.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        h2.setStyleSheet(f"font-size: 22px; font-weight: bold; color: {c['text']}; background: transparent; border: none;")
        il.addWidget(h2)
        d1 = QLabel(self.idle_desc())
        d1.setWordWrap(True)
        d1.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        d1.setStyleSheet(f"color: {c['text_secondary']}; background: transparent; border: none; font-size: 13px;")
        il.addWidget(d1)
        scan_btn = QPushButton(self.idle_btn_text())
        scan_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        scan_btn.setAutoDefault(False)
        scan_btn.setMinimumWidth(220)
        scan_btn.setStyleSheet(_primary(self.theme))
        scan_btn.clicked.connect(self._on_scan)
        il.addWidget(scan_btn, alignment=Qt.AlignmentFlag.AlignHCenter)
        stack.addWidget(idle)

        # page 1 : connect / qr
        qr = QWidget()
        qr.setStyleSheet("background: transparent;")
        ql = QVBoxLayout(qr)
        ql.setSpacing(8)
        holder = QVBoxLayout()
        holder.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        holder.setSpacing(10)
        ql.addLayout(holder)
        self._qr_label = QLabel("")
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setStyleSheet("background: transparent; border: none;")
        self._qr_label.setMinimumSize(210, 210)
        holder.addWidget(self._qr_label)
        self._timebar = QProgressBar()
        self._timebar.setMaximumHeight(8)
        self._timebar.setTextVisible(False)
        self._timebar.setVisible(self.uses_countdown())
        self._timebar.setStyleSheet(_progressbar(self.theme))
        holder.addWidget(self._timebar)
        self._qr_status = QLabel(self.qr_connecting_text())
        self._qr_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_status.setWordWrap(True)
        self._qr_status.setStyleSheet(f"color: {c['text']}; background: transparent; border: none; font-size: 13px;")
        holder.addWidget(self._qr_status)
        self._qr_steps = QLabel("")
        self._qr_steps.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._qr_steps.setWordWrap(True)
        self._qr_steps.setStyleSheet(f"color: {c['text_secondary']}; background: transparent; border: none; font-size: 13px;")
        holder.addWidget(self._qr_steps)
        ql.addStretch(1)
        stack.addWidget(qr)

        # page 2 : connected（有凭据 → 运行状态，不再要求重新扫码）
        conn = QWidget()
        conn.setStyleSheet("background: transparent;")
        cl = QVBoxLayout(conn)
        cl.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        cl.setSpacing(12)
        ck = QLabel("✅ 已接入")
        ck.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        ck.setStyleSheet(f"font-size: 22px; font-weight: bold; color: {c['success']}; background: transparent; border: none;")
        cl.addWidget(ck)
        self._conn_status = QLabel("未启动")
        self._conn_status.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._conn_status.setStyleSheet(f"color: {c['accent']}; background: transparent; border: none; font-size: 15px; font-weight: bold;")
        cl.addWidget(self._conn_status)
        self._conn_app = QLabel("")
        self._conn_app.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._conn_app.setWordWrap(True)
        self._conn_app.setStyleSheet(f"color: {c['text_secondary']}; background: transparent; border: none; font-size: 13px;")
        cl.addWidget(self._conn_app)
        dnote = QLabel("凭据已保存在本机。启动后即使退出应用，下次进来会自动重连，无需再次扫码。")
        dnote.setWordWrap(True)
        dnote.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        dnote.setStyleSheet(f"color: {c['text_hint']}; background: transparent; border: none; font-size: 12px;")
        cl.addWidget(dnote)
        # 已接入页只保留「重新扫码」；启停由上面运行设置的「启用网关」勾选统一承担（issue 2：合并重复控制）
        brow = QHBoxLayout()
        brow.setSpacing(10)
        rescan = QPushButton("重新扫码")
        rescan.setCursor(Qt.CursorShape.PointingHandCursor)
        rescan.setAutoDefault(False)
        rescan.setStyleSheet(_secondary(self.theme))
        rescan.clicked.connect(self._on_scan)
        brow.addStretch()
        brow.addWidget(rescan)
        brow.addStretch()
        cl.addLayout(brow)
        stack.addWidget(conn)

        root.addWidget(stack)
        self._states = stack
        self._idle_page, self._qr_page, self._connected_page = idle, qr, conn
        return frame

    # ── 连接管理器信号 ──────────────────────────────
    def _connect(self):
        # 状态/错误按 channel_key 分流：飞书面板只看飞书状态、微信面板只看微信状态（不串台）
        self.manager.notifier.status_changed.connect(self._on_status)
        self.manager.notifier.error.connect(self._on_error)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.cancel_provision)

    def _on_status(self, key: str, text: str):
        if key != self.channel_key():
            return
        self._status_lbl.setText(text)
        self._conn_status.setText(text)

    def _on_error(self, key: str, msg: str):
        if key != self.channel_key():
            return
        _tip(self, self.theme, msg)

    def cancel_provision(self):
        worker = getattr(self, "_scan_worker", None)
        if worker is not None:
            try:
                worker.cancel()
            except Exception:
                pass

    # ── 扫码事件 ──────────────────────────────────
    def _on_scan(self):
        if getattr(self, "_scan_worker", None) is not None:
            return
        self._states.setCurrentWidget(self._qr_page)
        self._set_qr_state_connecting()
        worker = self.make_scan_worker()
        self._scan_worker = worker
        worker.qr_ready.connect(self._on_qr_ready)
        worker.status_change.connect(self._on_provision_status)
        worker.done.connect(self._on_provision_done)
        worker.failed.connect(self._on_provision_failed)
        if isinstance(worker, QThread):
            worker.finished.connect(worker.deleteLater)
        worker.start()

    def _set_qr_state_connecting(self):
        self._stop_countdown()
        self._qr_label.clear()
        self._timebar.setValue(0)
        self._qr_status.setStyleSheet(
            f"color: {self.theme.colors['text']}; background: transparent; border: none; font-size: 13px;"
        )
        self._qr_status.setText(self.qr_connecting_text())
        self._qr_steps.setText("")
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(
            lambda: (self._qr_status.setText("长时间未完成，请检查网络后重试。"),
                     self._qr_status.setStyleSheet(
                         f"color: {self.theme.colors['danger']}; background: transparent; border: none; font-size: 13px;"
                     ))
        )
        self._watchdog.start(self.watchdog_ms())

    def _on_qr_ready(self, url: str, expire_in: int = 0):
        if self._watchdog:
            self._watchdog.stop()
        pix = _render_qr(url, 210) if url else None
        if pix is not None:
            self._qr_label.setPixmap(pix)
            self._qr_label.setStyleSheet("background: white; border: none; padding: 0;")
        else:
            self._qr_label.setText(url or f"（无法生成二维码，请复制链接用{self.channel_display()}打开）")
        self._qr_status.setStyleSheet(
            f"color: {self.theme.colors['accent']}; background: transparent; border: none; font-size: 13px;"
        )
        self._qr_status.setText(self.qr_ready_text())
        self._qr_steps.setText(self.qr_ready_steps())
        if self.uses_countdown() and expire_in and expire_in > 0:
            self._deadline = time_now() + max(0, expire_in)
            self._remaining = max(0, expire_in)
            self._timebar.setMaximum(max(1, self._remaining))
            self._timebar.setValue(self._remaining)
            self._countdown_timer.start()

    def _tick(self):
        self._remaining = max(0, int(self._deadline - time_now()))
        if self._remaining <= 0:
            self._countdown_timer.stop()
            self._qr_status.setText("二维码已过期，请重新生成。")
            self._timebar.setValue(0)
            return
        self._timebar.setValue(self._remaining)
        self._qr_status.setText(f"● 正在添加新机器人 — 二维码有效 {fmt_duration(self._remaining)}")

    def _stop_countdown(self):
        self._countdown_timer.stop()

    def _on_provision_status(self, status: str):
        mapping = self.status_map()
        if status in mapping:
            self._qr_status.setText(mapping[status])

    def _on_provision_done(self, *result):
        self._stop_countdown()
        if self._watchdog:
            self._watchdog.stop()
        self._scan_worker = None  # 终态释放 worker，允许「重新扫码」
        self.finish_provision(result)

    def _on_provision_failed(self, msg: str):
        self._stop_countdown()
        if self._watchdog:
            self._watchdog.stop()
        self._scan_worker = None  # 终态释放 worker，允许重试
        self._qr_status.setText(f"失败：{msg}")
        self._qr_status.setStyleSheet(
            f"color: {self.theme.colors['danger']}; background: transparent; border: none; font-size: 13px;"
        )
        self._states.setCurrentWidget(self._qr_page)
        _tip(self, self.theme, f"{self.channel_display()}扫码登录失败：{msg}")

    # ── 通用：启用/启停/保存/状态 ─────────────────────
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

    def _on_default_project_changed(self):
        if self._syncing:
            return
        cfg = self.store.get_config()
        self._write_default_project(cfg, self._default_project.currentData() or "")
        self.store.save_config(cfg)

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

    def refresh(self):
        cfg = self.store.get_config()
        self._syncing = True
        try:
            self._enabled.setChecked(cfg.channel_enabled(self.channel_key()))
            self._allow_from.setPlainText("\n".join(cfg.allow_from))
            self._default_project.clear()
            self._default_project.addItem("（未绑定）", "")
            for p in self.manager.project_store.list_projects():
                self._default_project.addItem(f"{p.title}（{p.id}）", p.id)
            did = self._read_default_project(cfg)
            idx = self._default_project.findData(did)
            if idx >= 0:
                self._default_project.setCurrentIndex(idx)
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


# ── 飞书接入面板 ────────────────────────────────────
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

    def _read_default_project(self, cfg: GatewayChannelConfig) -> str:
        return cfg.feishu_default_project_id

    def _write_default_project(self, cfg: GatewayChannelConfig, project_id: str) -> None:
        cfg.feishu_default_project_id = project_id

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


# ── 微信接入面板 ────────────────────────────────────
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

    def _read_default_project(self, cfg: GatewayChannelConfig) -> str:
        return cfg.wechat_default_project_id

    def _write_default_project(self, cfg: GatewayChannelConfig, project_id: str) -> None:
        cfg.wechat_default_project_id = project_id

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

# ── 共享小工具（Feishu / WeChat 面板共用） ────────────────
def _card_frame(theme: Theme, caption: str) -> tuple[QFrame, QVBoxLayout]:
    """返回 (frame, layout)：调用方用返回的 layout 往里加内容，不要另建新 layout，
    否则会触发 QLayout "already has a layout" 且内容不渲染。"""
    c = theme.colors
    frame = QFrame()
    frame.setStyleSheet(
        f"QFrame {{ background: {c['card_bg']}; border: 1px solid {c['border_light']}; border-radius: 8px; }}"
    )
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(8)
    if caption:
        lab = QLabel(caption)
        lab.setStyleSheet(
            f"color: {c['text']}; font-size: 14px; font-weight: bold;"
            "background: transparent; border: none;"
        )
        lay.addWidget(lab)
    return frame, lay


# ── 主工作区 ────────────────────────────────────────
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

__all__ = ["_FeishuPanel", "_WeChatPanel"]
