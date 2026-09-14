"""DeziBee 需求信息区：仅展示需求名称 + 运行状态。"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from tokbee.ui.styles.theme import Theme

from dezibee.core.models import Requirement


class ReqInfoPanel(QFrame):
    """需求信息区（需求名称 + 运行状态）。"""

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        c = theme.colors
        self.setStyleSheet(
            f"ReqInfoPanel {{ background: {c['card_bg']}; border-radius: 8px; "
            f"border: 1px solid {c['border_light']}; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)

        title_row = QHBoxLayout()
        self._title = QLabel("未选择需求")
        self._title.setStyleSheet(
            f"font-size: 15px; font-weight: bold; color: {c['text']};"
            "background: transparent; border: none;"
        )
        title_row.addWidget(self._title, 1)
        self._status = QLabel("")
        self._status.setStyleSheet(
            f"font-size: 12px; color: {c['text_hint']}; background: transparent; border: none;"
        )
        title_row.addWidget(self._status)
        layout.addLayout(title_row)

        # 需求编号：从列表项挪到这里，紧贴标题下方
        self._req_id = QLabel("")
        self._req_id.setStyleSheet(
            f"font-size: 11px; color: {c['text_hint']}; background: transparent; border: none;"
        )
        layout.addWidget(self._req_id)

        self.set_running(False, has_req=False)

    def set_req(self, req: Requirement | None):
        if req is None:
            self._title.setText("未选择需求")
            self._req_id.setText("")
            self._status.setText("")
            return
        self._title.setText(req.title)
        self._req_id.setText(req.id)
        # 运行状态由 set_running 刷新；切需求时按空闲显示
        self.set_running(False, has_req=True)

    def set_running(self, running: bool, *, has_req: bool = True):
        """运行状态：未选需求不显示；已选显示「运行中 / 空闲」。"""
        c = self.theme.colors
        if not has_req:
            self._status.setText("")
            return
        if running:
            self._status.setText("● 运行中")
            self._status.setStyleSheet(
                f"font-size: 12px; font-weight: bold; color: {c['accent']};"
                "background: transparent; border: none;"
            )
        else:
            self._status.setText("空闲")
            self._status.setStyleSheet(
                f"font-size: 12px; color: {c['text_hint']};"
                "background: transparent; border: none;"
            )
