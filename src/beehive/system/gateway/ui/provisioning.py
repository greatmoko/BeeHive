"""扫码连接、二维码与超时显示的控制器。"""
from __future__ import annotations
from PySide6.QtCore import QThread, QTimer
from beehive.common.ui.dialogs.message import show_tip as _tip
from beehive.system.gateway.ui.styles import _render_qr, time_now, fmt_duration
from PySide6.QtCore import QObject
class ProvisionController(QObject):
    """持有扫码工作对象、超时与倒计时；面板负责配置和显示。"""
    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self._scan_worker = None
        self._watchdog = None
        self._countdown_timer = QTimer(panel)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._tick)
        self._deadline = 0.0
        self._remaining = 0

    def cancel_provision(self):
        panel = self.panel
        worker = getattr(self, "_scan_worker", None)
        if worker is not None:
            try:
                worker.cancel()
            except Exception:
                pass

    def _on_scan(self):
        panel = self.panel
        if getattr(self, "_scan_worker", None) is not None:
            return
        panel._states.setCurrentWidget(panel._qr_page)
        self._set_qr_state_connecting()
        worker = panel.make_scan_worker()
        self._scan_worker = worker
        worker.qr_ready.connect(self._on_qr_ready)
        worker.status_change.connect(self._on_provision_status)
        worker.done.connect(self._on_provision_done)
        worker.failed.connect(self._on_provision_failed)
        if isinstance(worker, QThread):
            worker.finished.connect(worker.deleteLater)
        worker.start()

    def _set_qr_state_connecting(self):
        panel = self.panel
        self._stop_countdown()
        panel._qr_label.clear()
        panel._timebar.setValue(0)
        panel._qr_status.setStyleSheet(
            f"color: {panel.theme.colors['text']}; background: transparent; border: none; font-size: 13px;"
        )
        panel._qr_status.setText(panel.qr_connecting_text())
        panel._qr_steps.setText("")
        self._watchdog = QTimer(panel)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(
            lambda: (panel._qr_status.setText("长时间未完成，请检查网络后重试。"),
                     panel._qr_status.setStyleSheet(
                         f"color: {panel.theme.colors['danger']}; background: transparent; border: none; font-size: 13px;"
                     ))
        )
        self._watchdog.start(panel.watchdog_ms())

    def _on_qr_ready(self, url: str, expire_in: int = 0):
        panel = self.panel
        if self._watchdog:
            self._watchdog.stop()
        pix = _render_qr(url, 210) if url else None
        if pix is not None:
            panel._qr_label.setPixmap(pix)
            panel._qr_label.setStyleSheet("background: white; border: none; padding: 0;")
        else:
            panel._qr_label.setText(url or f"（无法生成二维码，请复制链接用{panel.channel_display()}打开）")
        panel._qr_status.setStyleSheet(
            f"color: {panel.theme.colors['accent']}; background: transparent; border: none; font-size: 13px;"
        )
        panel._qr_status.setText(panel.qr_ready_text())
        panel._qr_steps.setText(panel.qr_ready_steps())
        if panel.uses_countdown() and expire_in and expire_in > 0:
            self._deadline = time_now() + max(0, expire_in)
            self._remaining = max(0, expire_in)
            panel._timebar.setMaximum(max(1, self._remaining))
            panel._timebar.setValue(self._remaining)
            self._countdown_timer.start()

    def _tick(self):
        panel = self.panel
        self._remaining = max(0, int(self._deadline - time_now()))
        if self._remaining <= 0:
            self._countdown_timer.stop()
            panel._qr_status.setText("二维码已过期，请重新生成。")
            panel._timebar.setValue(0)
            return
        panel._timebar.setValue(self._remaining)
        panel._qr_status.setText(f"● 正在添加新机器人 — 二维码有效 {fmt_duration(self._remaining)}")

    def _stop_countdown(self):
        panel = self.panel
        self._countdown_timer.stop()

    def _on_provision_status(self, status: str):
        panel = self.panel
        mapping = panel.status_map()
        if status in mapping:
            panel._qr_status.setText(mapping[status])

    def _on_provision_done(self, *result):
        panel = self.panel
        self._stop_countdown()
        if self._watchdog:
            self._watchdog.stop()
        self._scan_worker = None  # 终态释放 worker，允许「重新扫码」
        panel.finish_provision(result)

    def _on_provision_failed(self, msg: str):
        panel = self.panel
        self._stop_countdown()
        if self._watchdog:
            self._watchdog.stop()
        self._scan_worker = None  # 终态释放 worker，允许重试
        panel._qr_status.setText(f"失败：{msg}")
        panel._qr_status.setStyleSheet(
            f"color: {panel.theme.colors['danger']}; background: transparent; border: none; font-size: 13px;"
        )
        panel._states.setCurrentWidget(panel._qr_page)
        _tip(panel, panel.theme, f"{panel.channel_display()}扫码登录失败：{msg}")
