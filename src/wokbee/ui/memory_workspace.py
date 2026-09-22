"""Memory settings and explicit, default-no global memory proposals."""
import sqlite3
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea,
    QLineEdit, QSizePolicy, QSpinBox, QTextEdit, QVBoxLayout, QWidget,
)

from wokbee.core.memory import MODULES, MemoryStore


class MemoryProposalDialog(QDialog):
    def __init__(self, proposal, store, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("全局记忆更新建议")
        self.resize(600, 480)
        self.store, self.proposal = store, proposal
        # Override the app-wide input stylesheet so this read-only view stays
        # readable when the surrounding theme uses a dark editor palette.
        self.setStyleSheet("""
            QDialog { background: #f7f8fa; color: #202124; }
            QLabel { color: #202124; background: transparent; }
            QTextEdit { background: #ffffff; color: #202124;
                selection-background-color: #cfe3ff; selection-color: #202124;
                border: 1px solid #c7cbd1; border-radius: 4px; }
            QPushButton { background: #ffffff; color: #202124;
                border: 1px solid #b9bec7; border-radius: 4px; padding: 4px 18px; }
        """)
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
        global_card = QGroupBox("全局记忆（单份）")
        global_layout = QVBoxLayout(global_card)
        global_layout.addWidget(QLabel("可直接编辑并保存，AI 建议仍需手动确认。"))
        self.content = QTextEdit()
        self.content.setStyleSheet(
            "QTextEdit { background: #ffffff; color: #202124; "
            "selection-background-color: #cfe3ff; selection-color: #202124; "
            "border: 1px solid #c7cbd1; border-radius: 4px; }"
        )
        global_layout.addWidget(self.content)
        save_memory = QPushButton("保存全局记忆")
        save_memory.clicked.connect(self.save_global_memory)
        global_layout.addWidget(save_memory)
        root.addWidget(global_card, 1)

        atomic_card = QGroupBox("原子记忆（关键词组与完整记忆）")
        atomic_layout = QVBoxLayout(atomic_card)
        atomic_layout.addWidget(QLabel("默认显示最近 10 条；输入关键词后只显示相关记忆。"))
        search_row = QHBoxLayout()
        self.atomic_query = QLineEdit()
        self.atomic_query.setPlaceholderText("输入关键词，多个关键词用空格或逗号分隔")
        search_row.addWidget(self.atomic_query)
        atomic_search = QPushButton("查询")
        atomic_search.clicked.connect(self.refresh_atomic_memories)
        search_row.addWidget(atomic_search)
        atomic_layout.addLayout(search_row)
        atomic_scroll = QScrollArea()
        atomic_scroll.setWidgetResizable(True)
        atomic_scroll.setMaximumHeight(190)
        atomic_container = QWidget()
        self.atomic_results = QVBoxLayout(atomic_container)
        self.atomic_results.setContentsMargins(4, 4, 4, 4)
        atomic_scroll.setWidget(atomic_container)
        atomic_layout.addWidget(atomic_scroll)
        clear_atomic = QPushButton("清空全部原子记忆")
        clear_atomic.clicked.connect(self.clear_atomic_memories)
        atomic_layout.addWidget(clear_atomic)
        root.addWidget(atomic_card, 1)
        restore_row = QHBoxLayout()
        refresh = QPushButton("刷新建议")
        refresh.clicked.connect(self.refresh)
        restore_row.addWidget(refresh)
        root.addLayout(restore_row)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        self.pending = QVBoxLayout(container)
        scroll.setWidget(container)
        root.addWidget(scroll)
        self.refresh()
        self.refresh_atomic_memories()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def refresh(self):
        trigger, target = self.store.thresholds()
        self.trigger.setValue(round(trigger * 100))
        self.target.setValue(round(target * 100))
        snapshot = self.store.global_memory()
        text = self.store.global_text(snapshot)
        self.content.setPlainText(text)
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

    def refresh_atomic_memories(self):
        while self.atomic_results.count():
            item = self.atomic_results.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                while item.layout().count():
                    child = item.layout().takeAt(0)
                    if child.widget():
                        child.widget().deleteLater()
        query = self.atomic_query.text().replace(",", " ").split()
        rows = self.store.search_full(query, 10) if query else self.store.recent(10)
        if not rows:
            self.atomic_results.addWidget(QLabel("没有匹配的原子记忆。"))
            return
        for item in rows:
            # Search results intentionally expose enough context for deletion,
            # while keeping the full body out of the default list.
            ident = item["id"]
            text = f"{item['id']}  ·  {item['type']}  ·  关键词组：{', '.join(item['keywords'])}\n完整记忆：{item['body']}"
            label = QLabel(text)
            label.setWordWrap(True)
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            row_widget = QWidget()
            row_widget.setMinimumHeight(34)
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(label, 1)
            remove = QPushButton("删除")
            remove.setFixedWidth(58)
            remove.clicked.connect(lambda _, key=ident: self.delete_atomic_memory(key))
            row.addWidget(remove)
            self.atomic_results.addWidget(row_widget)

    def delete_atomic_memory(self, ident):
        answer = QMessageBox.question(self, "删除原子记忆", "确定删除这条原子记忆吗？此操作不可恢复。",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.delete(ident)
        except (ValueError, OSError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "无法删除", str(exc))
            return
        self.refresh_atomic_memories()

    def clear_atomic_memories(self):
        answer = QMessageBox.question(self, "清空原子记忆", "确定删除全部原子记忆吗？此操作不可恢复。",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.store.clear_atomic()
            self.refresh_atomic_memories()

    def open_proposal(self, proposal):
        dialog = MemoryProposalDialog(proposal, self.store, self)
        dialog.finished.connect(lambda _: self.refresh())
        dialog.show()

    def save_thresholds(self):
        try:
            self.store.set_thresholds(self.trigger.value() / 100, self.target.value() / 100)
        except ValueError as exc:
            QMessageBox.warning(self, "无法保存", str(exc))

    def save_global_memory(self):
        try:
            blocks = self.content.toPlainText().split("\n\n")
            parsed = {}
            for block in blocks:
                lines = block.splitlines()
                if lines and lines[0].startswith("【") and lines[0].endswith("】"):
                    parsed[lines[0][1:-1]] = "\n".join(lines[1:]).strip()
            changed = self.store.save_global({name: parsed.get(name, "") for name in MODULES})
        except (ValueError, OSError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "无法保存全局记忆", str(exc))
            return
        self.refresh()
        QMessageBox.information(self, "已保存", "全局记忆已保存。" if changed else "内容未变化。")
