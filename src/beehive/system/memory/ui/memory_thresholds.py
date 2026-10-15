"""压缩阈值表单，点击保存时才调用存储校验。"""
import sqlite3
from types import SimpleNamespace

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSpinBox, QWidget
from beehive.common.ui.dialogs.message import show_tip as _tip
from beehive.common.ui.styles.buttons import apply_primary_btn
from beehive.common.ui.styles.inputs import apply_spin


class MemoryThresholds(QWidget):
    def __init__(self, colors, store, parent=None):
        super().__init__(parent)
        self._colors, self.store = colors, store
        self._theme = SimpleNamespace(colors=colors)
        threshold_row = self
        row = QHBoxLayout(threshold_row)
        self.trigger, self.target = QSpinBox(), QSpinBox()
        for spin in (self.trigger, self.target):
            spin.setRange(1, 100)
            spin.setSuffix("%")
            apply_spin(spin, self._colors)
        row.addWidget(QLabel("上下文触发"))
        row.addWidget(self.trigger)
        row.addWidget(QLabel("压缩目标"))
        row.addWidget(self.target)
        save = QPushButton("保存阈值")
        apply_primary_btn(save, self._colors)
        save.setFixedWidth(120)
        save.clicked.connect(self.save_thresholds)
        row.addWidget(save)

    def refresh(self):
        trigger, target = self.store.thresholds()
        self.trigger.setValue(round(trigger * 100))
        self.target.setValue(round(target * 100))

    def save_thresholds(self):
        try:
            self.store.set_thresholds(self.trigger.value() / 100, self.target.value() / 100)
        except ValueError as exc:
            _tip(self, self._theme, str(exc), title="无法保存")
