"""Memory settings and explicit, default-no global memory proposals."""
import sqlite3
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea,
    QSpinBox, QTextEdit, QVBoxLayout, QWidget,
)

from wokbee.core.memory import MemoryStore


class MemoryProposalDialog(QDialog):
    def __init__(self, proposal, store, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("全局记忆更新建议")
        self.resize(600, 480)
        self.store, self.proposal = store, proposal
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"修改模块：{proposal['module']}"))
        details = QTextEdit()
        details.setReadOnly(True)
        details.setPlainText(f"原内容：\n{proposal['old'] or '（空）'}\n\n建议新内容：\n{proposal['new']}\n\n修改原因：\n{proposal['reason']}")
        layout.addWidget(details)
        row = QHBoxLayout()
        row.addWidget(QLabel("是否应用？默认：否"))
        row.addStretch()
        yes, no = QPushButton("是"), QPushButton("否")
        yes.setAutoDefault(False)
        no.setDefault(True)
        yes.clicked.connect(lambda: self.decide(True))
        no.clicked.connect(lambda: self.decide(False))
        row.addWidget(yes)
        row.addWidget(no)
        layout.addLayout(row)

    def decide(self, accept):
        try:
            self.store.decide(self.proposal["id"], accept=accept)
        except (ValueError, OSError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "未应用更新", str(exc))
            return
        self.accept()


def show_memory_proposal(ident, parent):
    store = MemoryStore()
    proposal = next((p for p in store.proposals() if p["id"] == ident), None)
    if proposal:
        dialog = MemoryProposalDialog(proposal, store, parent)
        # Parent owns the non-modal component. Closing it never applies the proposal.
        dialog.show()


class MemoryWorkspace(QWidget):
    def __init__(self, theme, parent=None, *, store=None):
        super().__init__(parent)
        self.store = store or MemoryStore()
        root = QVBoxLayout(self)
        root.addWidget(QLabel("记忆系统 · WokBee / DeziBee"))
        row = QHBoxLayout()
        self.trigger, self.target = QSpinBox(), QSpinBox()
        for spin in (self.trigger, self.target):
            spin.setRange(1, 100)
            spin.setSuffix("%")
        row.addWidget(QLabel("上下文触发"))
        row.addWidget(self.trigger)
        row.addWidget(QLabel("压缩目标"))
        row.addWidget(self.target)
        save = QPushButton("保存阈值")
        save.clicked.connect(self.save_thresholds)
        row.addWidget(save)
        root.addLayout(row)
        self.version_label = QLabel()
        root.addWidget(self.version_label)
        self.content = QTextEdit()
        self.content.setReadOnly(True)
        root.addWidget(self.content, 1)
        restore_row = QHBoxLayout()
        self.version = QSpinBox()
        self.version.setMinimum(1)
        restore_row.addWidget(QLabel("恢复历史版本"))
        restore_row.addWidget(self.version)
        restore = QPushButton("恢复此版本")
        restore.clicked.connect(self.restore)
        restore_row.addWidget(restore)
        refresh = QPushButton("刷新建议")
        refresh.clicked.connect(self.refresh)
        restore_row.addWidget(refresh)
        root.addLayout(restore_row)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        self.pending = QVBoxLayout(container)
        scroll.setWidget(container)
        root.addWidget(scroll, 1)
        self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        trigger, target = self.store.thresholds()
        self.trigger.setValue(round(trigger * 100))
        self.target.setValue(round(target * 100))
        snapshot = self.store.global_memory()
        text = self.store.global_text(snapshot)
        self.version_label.setText(f"全局记忆 v{snapshot['version']} · {len(text)}/5000 字")
        self.content.setPlainText(text)
        self.version.setMaximum(snapshot["version"])
        self.version.setValue(max(1, snapshot["version"] - 1))
        while self.pending.count():
            item = self.pending.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for proposal in self.store.proposals():
            label = QLabel(f"{proposal['module']}\n原内容：{proposal['old'] or '（空）'}\n建议新内容：{proposal['new']}\n修改原因：{proposal['reason']}")
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            self.pending.addWidget(label)
            button = QPushButton("查看并选择 是 / 否（默认否）")
            button.clicked.connect(lambda _, p=proposal: self.open_proposal(p))
            self.pending.addWidget(button)
        self.pending.addStretch()

    def open_proposal(self, proposal):
        dialog = MemoryProposalDialog(proposal, self.store, self)
        dialog.finished.connect(lambda _: self.refresh())
        dialog.show()

    def save_thresholds(self):
        try:
            self.store.set_thresholds(self.trigger.value() / 100, self.target.value() / 100)
        except ValueError as exc:
            QMessageBox.warning(self, "无法保存", str(exc))

    def restore(self):
        answer = QMessageBox.question(self, "恢复全局记忆", f"将版本 {self.version.value()} 恢复为一个新版本？",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.store.restore(self.version.value())
            self.refresh()
