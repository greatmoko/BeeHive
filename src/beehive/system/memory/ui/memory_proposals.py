"""记忆更新提案显示与用户明确选择，显示时不应用提案。"""
import sqlite3
from types import SimpleNamespace
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QScrollArea, QTextEdit, QVBoxLayout, QWidget
from beehive.common.ui.styles.buttons import apply_danger_btn, apply_primary_btn, apply_secondary_btn
from beehive.common.ui.styles.inputs import apply_textedit
from beehive.common.ui.dialogs.message import show_tip as _tip
from beehive.common.ui.theme import COLORS
from beehive.system.memory.ui.memory_document import _apply_memory_dialog_surface


class MemoryProposalDialog(QDialog):
    def __init__(self, proposal, store, parent=None, *, colors=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("全局记忆更新建议")
        self.resize(600, 480)
        self.store, self.proposal = store, proposal
        self._colors = colors or COLORS
        self._theme = SimpleNamespace(colors=self._colors)
        _apply_memory_dialog_surface(self, self._colors)
        layout = QVBoxLayout(self)
        operation = "新增规则" if proposal.get("operation") == "add" else "替换规则"
        layout.addWidget(QLabel(f"修改模块：{proposal['module']}（{operation}）"))
        details = QTextEdit()
        details.setReadOnly(True)
        details.setPlainText(f"目标规则：\n{proposal['old'] or '（新增，无目标规则）'}\n\n建议规则：\n{proposal['new']}\n\n修改原因：\n{proposal['reason']}")
        apply_textedit(details, self._colors)
        layout.addWidget(details)
        row = QHBoxLayout()
        row.addWidget(QLabel("是否应用？默认：否"))
        row.addStretch()
        yes, no = QPushButton("是"), QPushButton("否")
        apply_primary_btn(yes, self._colors)
        apply_secondary_btn(no, self._colors)
        yes.setFixedWidth(56)
        no.setFixedWidth(56)
        yes.clicked.connect(lambda: self.decide(True))
        no.clicked.connect(lambda: self.decide(False))
        row.addWidget(yes)
        row.addWidget(no)
        layout.addLayout(row)

    def decide(self, accept):
        try:
            self.store.decide(self.proposal["id"], accept=accept)
        except (ValueError, OSError, sqlite3.Error) as exc:
            _tip(self, self._theme, str(exc), title="未应用更新")
            return
        self.accept()


class MemoryProposals(QWidget):
    changed = Signal()
    def __init__(self, colors, store, parent=None):
        super().__init__(parent)
        self._colors, self.store = colors, store
        self._theme = SimpleNamespace(colors=colors)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        self.pending = QVBoxLayout(container)
        scroll.setWidget(container)
        layout.addWidget(scroll)

    def refresh(self):
        while self.pending.count():
            item = self.pending.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        proposals = self.store.proposals()
        if proposals:
            self.pending.addWidget(QLabel("待处理记忆更新"))
        for proposal in proposals:
            operation = "新增" if proposal.get("operation") == "add" else "替换"
            card = QWidget()
            card.setObjectName("memoryProposalCard")
            card.setStyleSheet(f"#memoryProposalCard {{ border: 1px solid {self._colors['border']}; border-radius: 6px; }}")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(8, 6, 8, 6)
            label = QLabel(f"{proposal['module']} · {operation}\n目标规则：{proposal['old'] or '（新增）'}\n建议规则：{proposal['new']}\n修改原因：{proposal['reason']}")
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            card_layout.addWidget(label)
            actions_layout = QHBoxLayout()
            actions_layout.setContentsMargins(0, 0, 0, 0)
            view = QPushButton("查看详情")
            apply_secondary_btn(view, self._colors)
            view.setFixedWidth(88)
            view.clicked.connect(lambda _, p=proposal: self.open_proposal(p))
            accept = QPushButton("一键采纳")
            apply_primary_btn(accept, self._colors)
            accept.setFixedWidth(96)
            accept.clicked.connect(
                lambda _, ident=proposal["id"]: self.decide_proposal(ident, accept=True)
            )
            discard = QPushButton("一键废弃")
            apply_danger_btn(discard, self._colors)
            discard.setFixedWidth(96)
            discard.clicked.connect(
                lambda _, ident=proposal["id"]: self.decide_proposal(ident, accept=False)
            )
            actions_layout.addWidget(view)
            actions_layout.addWidget(accept)
            actions_layout.addWidget(discard)
            actions_layout.addStretch()
            card_layout.addLayout(actions_layout)
            self.pending.addWidget(card)
        self.pending.addStretch()

    def open_proposal(self, proposal):
        dialog = MemoryProposalDialog(proposal, self.store, self, colors=self._colors)
        dialog.finished.connect(self.changed)
        dialog.show()

    def decide_proposal(self, ident, *, accept):
        try:
            self.store.decide(ident, accept=accept)
        except (ValueError, OSError, sqlite3.Error) as exc:
            _tip(self, self._theme, str(exc), title="无法处理记忆更新")
            return
        self.changed.emit()
