"""DeziBee 需求信息区：展示当前需求基本信息。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from tokbee.ui.styles.theme import Theme

from dezibee.core.models import Requirement


class _InfoRow(QWidget):
    def __init__(self, label: str, value: str, theme: Theme, parent=None):
        super().__init__(parent)
        c = theme.colors
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 2, 0, 2)
        h.setSpacing(8)
        key = QLabel(label)
        key.setFixedWidth(64)
        key.setStyleSheet(
            f"font-size: 12px; color: {c['text_hint']}; background: transparent; border: none;"
        )
        h.addWidget(key)
        val = QLabel(value or "—")
        val.setWordWrap(True)
        val.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        val.setStyleSheet(
            f"font-size: 12px; color: {c['text']}; background: transparent; border: none;"
        )
        h.addWidget(val, 1)
        self._val = val

    def set_value(self, value: str):
        self._val.setText(value or "—")


class ReqInfoPanel(QFrame):
    """需求信息区（需求名称/ID/状态/描述/创建/更新）。"""

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
        layout.setSpacing(4)

        title_row = QHBoxLayout()
        self._title = QLabel("未选择需求")
        self._title.setStyleSheet(
            f"font-size: 15px; font-weight: bold; color: {c['text']};"
            "background: transparent; border: none;"
        )
        title_row.addWidget(self._title, 1)
        self._status = QLabel("")
        self._status.setStyleSheet(
            f"font-size: 11px; color: {c['accent']}; background: transparent; border: none;"
        )
        title_row.addWidget(self._status)
        layout.addLayout(title_row)

        self._id_row = _InfoRow("需求ID", "", theme)
        self._created_row = _InfoRow("创建时间", "", theme)
        self._updated_row = _InfoRow("更新时间", "", theme)
        layout.addWidget(self._id_row)
        layout.addWidget(self._created_row)
        layout.addWidget(self._updated_row)

        self._desc_label = QLabel("")
        self._desc_label.setWordWrap(True)
        self._desc_label.setStyleSheet(
            f"font-size: 12px; color: {c['text_secondary']};"
            "background: transparent; border: none;"
        )
        layout.addWidget(self._desc_label)

    def set_req(self, req: Requirement | None):
        if req is None:
            self._title.setText("未选择需求")
            self._status.setText("")
            self._id_row.set_value("")
            self._created_row.set_value("")
            self._updated_row.set_value("")
            self._desc_label.setText("")
            return
        self._title.setText(req.title)
        self._status.setText(
            "进行中" if req.status == "active" else req.status or ""
        )
        self._id_row.set_value(req.id)
        self._created_row.set_value(req.created_at)
        self._updated_row.set_value(req.updated_at)
        self._desc_label.setText(req.description or "")
