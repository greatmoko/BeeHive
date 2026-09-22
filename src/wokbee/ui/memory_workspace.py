"""Memory settings and explicit, default-no global memory proposals."""
import sqlite3
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton, QScrollArea,
    QHeaderView, QLineEdit, QSizePolicy, QSpinBox, QTextEdit, QTableWidget,
    QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from wokbee.core.memory import MODULES, MemoryStore
from tokbee.ui.styles.system import (
    apply_danger_btn,
    apply_lineedit,
    apply_primary_btn,
    apply_secondary_btn,
    apply_spin,
    apply_textedit,
)
from tokbee.ui.styles.theme import COLORS


def _apply_memory_dialog_surface(dialog, colors):
    background = colors.get("card_bg", colors.get("content_bg", "#f8f8f8"))
    text = colors.get("text", "#1a1a1a")
    dialog.setStyleSheet(
        f"QDialog {{ background-color: {background}; color: {text}; }}"
        f" QLabel {{ color: {text}; background: transparent; }}"
    )


def _parse_global_memory_text(text):
    """Split the editable document on module headings, never on blank lines."""
    parts = {name: [] for name in MODULES}
    current = None
    headings = {f"【{name}】": name for name in MODULES}
    for line in str(text or "").splitlines():
        current = headings.get(line.strip(), current)
        if line.strip() in headings:
            continue
        if current is not None:
            parts[current].append(line)
    return {name: "\n".join(parts[name]).strip() for name in MODULES}


class MemoryProposalDialog(QDialog):
    def __init__(self, proposal, store, parent=None, *, colors=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("全局记忆更新建议")
        self.resize(600, 480)
        self.store, self.proposal = store, proposal
        self._colors = colors or COLORS
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
            QMessageBox.warning(self, "未应用更新", str(exc))
            return
        self.accept()


class MemoryWorkspace(QWidget):
    def __init__(self, theme, parent=None, *, store=None):
        super().__init__(parent)
        self._colors = getattr(theme, "colors", COLORS)
        self.store = store or MemoryStore()
        self._sync_initial_environment()
        self._atomic_rows = []
        self._atomic_page = 1
        self._atomic_total_pages = 1
        root = QVBoxLayout(self)
        self.memory_title = QLabel("记忆系统 · WokBee / DeziBee")
        root.addWidget(self.memory_title)
        threshold_row = QWidget()
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
        self.threshold_row = threshold_row
        root.addWidget(threshold_row)
        global_card = QWidget()
        self.global_card = global_card
        global_layout = QVBoxLayout(global_card)
        global_layout.addWidget(QLabel("全局记忆（单份）"))
        global_layout.addWidget(QLabel("可直接编辑并保存，AI 建议仍需手动确认。"))
        self.content = QTextEdit()
        apply_textedit(self.content, self._colors)
        global_layout.addWidget(self.content)
        save_memory = QPushButton("保存全局记忆")
        apply_primary_btn(save_memory, self._colors)
        save_memory.setFixedWidth(140)
        save_memory.clicked.connect(self.save_global_memory)
        root.addWidget(global_card, 1)

        atomic_card = QWidget()
        self.atomic_card = atomic_card
        atomic_layout = QVBoxLayout(atomic_card)
        atomic_layout.addWidget(QLabel("原子记忆（关键词组与完整记忆）"))
        atomic_layout.addWidget(QLabel("默认显示最近 10 条；输入关键词后只显示相关记忆。"))
        search_row = QHBoxLayout()
        self.atomic_query = QLineEdit()
        self.atomic_query.setPlaceholderText("输入关键词，多个关键词用空格或逗号分隔")
        apply_lineedit(self.atomic_query, self._colors)
        search_row.addWidget(self.atomic_query)
        atomic_search = QPushButton("查询")
        apply_secondary_btn(atomic_search, self._colors)
        atomic_search.setFixedWidth(72)
        atomic_search.clicked.connect(lambda: self.refresh_atomic_memories(reset_page=True))
        search_row.addWidget(atomic_search)
        atomic_layout.addLayout(search_row)
        self.atomic_table = QTableWidget(0, 5)
        self.atomic_table.setHorizontalHeaderLabels([
            "ID", "类型", "关键词组", "调取次数", "操作",
        ])
        self.atomic_table.setMinimumHeight(180)
        self.atomic_table.setAlternatingRowColors(True)
        self.atomic_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.atomic_table.setWordWrap(False)
        self.atomic_table.verticalHeader().setVisible(False)
        header = self.atomic_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        atomic_layout.addWidget(self.atomic_table)

        page_row = QHBoxLayout()
        self.atomic_prev = QPushButton("上一页")
        apply_secondary_btn(self.atomic_prev, self._colors)
        self.atomic_prev.setFixedWidth(72)
        self.atomic_prev.clicked.connect(lambda: self._set_atomic_page(self._atomic_page - 1))
        page_row.addWidget(self.atomic_prev)
        self.atomic_next = QPushButton("下一页")
        apply_secondary_btn(self.atomic_next, self._colors)
        self.atomic_next.setFixedWidth(72)
        self.atomic_next.clicked.connect(lambda: self._set_atomic_page(self._atomic_page + 1))
        page_row.addWidget(self.atomic_next)
        page_row.addWidget(QLabel("每页数量"))
        self.atomic_page_size = QSpinBox()
        self.atomic_page_size.setRange(1, 50)
        self.atomic_page_size.setValue(10)
        self.atomic_page_size.setFixedWidth(64)
        apply_spin(self.atomic_page_size, self._colors)
        self.atomic_page_size.valueChanged.connect(
            lambda _: self.refresh_atomic_memories(reset_page=True)
        )
        page_row.addWidget(self.atomic_page_size)
        self.atomic_page_label = QLabel("总页数：1（当前第 1 页）")
        page_row.addWidget(self.atomic_page_label)
        page_row.addWidget(QLabel("跳转"))
        self.atomic_page_jump = QSpinBox()
        self.atomic_page_jump.setRange(1, 1)
        self.atomic_page_jump.setFixedWidth(64)
        apply_spin(self.atomic_page_jump, self._colors)
        page_row.addWidget(self.atomic_page_jump)
        page_row.addWidget(QLabel("页"))
        jump = QPushButton("确定")
        apply_secondary_btn(jump, self._colors)
        jump.setFixedWidth(56)
        jump.clicked.connect(lambda: self._set_atomic_page(self.atomic_page_jump.value()))
        page_row.addWidget(jump)
        page_row.addStretch()
        atomic_layout.addLayout(page_row)
        clear_atomic = QPushButton("清空全部原子记忆")
        apply_danger_btn(clear_atomic, self._colors)
        clear_atomic.setFixedWidth(160)
        clear_atomic.clicked.connect(self.clear_atomic_memories)
        atomic_layout.addWidget(clear_atomic)
        root.addWidget(atomic_card, 1)
        actions_row = QWidget()
        actions_layout = QHBoxLayout(actions_row)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.addStretch()
        actions_layout.addWidget(save_memory)
        refresh = QPushButton("刷新记忆")
        apply_secondary_btn(refresh, self._colors)
        refresh.setFixedWidth(100)
        refresh.clicked.connect(self.refresh)
        actions_layout.addWidget(refresh)
        reset_memory = QPushButton("一键重置")
        apply_danger_btn(reset_memory, self._colors)
        reset_memory.setFixedWidth(100)
        reset_memory.clicked.connect(self.reset_global_memory)
        actions_layout.addWidget(reset_memory)
        self.refresh_row = actions_row
        root.addWidget(actions_row)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.pending_scroll = scroll
        container = QWidget()
        self.pending = QVBoxLayout(container)
        scroll.setWidget(container)
        root.addWidget(scroll)
        self.refresh()
        self.refresh_atomic_memories()

    def _settings_environment(self):
        try:
            from wokbee.engine.runtime_env import build_runtime_env_settings_text

            text = build_runtime_env_settings_text().strip()
        except Exception:
            return ""
        if not text or text.startswith("尚未探测本机环境"):
            return ""
        return text

    def _sync_initial_environment(self):
        environment = self._settings_environment()
        if environment:
            try:
                self.store.ensure_environment(environment)
            except (ValueError, OSError, sqlite3.Error):
                pass


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
        proposals = self.store.proposals()
        if proposals:
            self.pending.addWidget(QLabel("待处理记忆更新"))
        for proposal in proposals:
            operation = "新增" if proposal.get("operation") == "add" else "替换"
            card = QWidget()
            card.setObjectName("memoryProposalCard")
            card.setStyleSheet("#memoryProposalCard { border: 1px solid #d8dee9; border-radius: 6px; }")
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

    def refresh_atomic_memories(self, *, reset_page=True):
        query = self.atomic_query.text().replace(",", " ").split()
        rows = self.store.search_full(query, 50) if query else self.store.recent(50)
        self._atomic_rows = rows
        self._atomic_total_pages = max(
            1, (len(rows) + self.atomic_page_size.value() - 1) // self.atomic_page_size.value()
        )
        if reset_page:
            self._atomic_page = 1
        self._set_atomic_page(self._atomic_page)

    def _set_atomic_page(self, page):
        self._atomic_page = max(1, min(self._atomic_total_pages, int(page)))
        self.atomic_page_jump.blockSignals(True)
        self.atomic_page_jump.setRange(1, self._atomic_total_pages)
        self.atomic_page_jump.setValue(self._atomic_page)
        self.atomic_page_jump.blockSignals(False)
        self.atomic_page_label.setText(
            f"总页数：{self._atomic_total_pages}（当前第 {self._atomic_page} 页）"
        )
        self.atomic_prev.setEnabled(self._atomic_page > 1)
        self.atomic_next.setEnabled(self._atomic_page < self._atomic_total_pages)
        self.atomic_table.setRowCount(0)
        size = self.atomic_page_size.value()
        rows = self._atomic_rows[(self._atomic_page - 1) * size:self._atomic_page * size]
        for row, item in enumerate(rows):
            self.atomic_table.insertRow(row)
            self.atomic_table.setItem(row, 0, QTableWidgetItem(str(item["id"])))
            self.atomic_table.setItem(row, 1, QTableWidgetItem(item["type"]))
            self.atomic_table.setItem(row, 2, QTableWidgetItem(", ".join(item["keywords"])))
            self.atomic_table.setItem(
                row, 3, QTableWidgetItem(str(item.get("retrieval_count", 0)))
            )
            actions = QWidget()
            action_layout = QHBoxLayout(actions)
            action_layout.setContentsMargins(6, 4, 6, 4)
            view = QPushButton("查看")
            apply_secondary_btn(view, self._colors)
            view.setFixedWidth(64)
            view.clicked.connect(lambda _, value=item: self.show_atomic_detail(value))
            remove = QPushButton("删除")
            apply_danger_btn(remove, self._colors)
            remove.setFixedWidth(64)
            remove.clicked.connect(lambda _, key=item["id"]: self.delete_atomic_memory(key))
            action_layout.addWidget(view)
            action_layout.addWidget(remove)
            action_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.atomic_table.setCellWidget(row, 4, actions)
            self.atomic_table.setRowHeight(row, 42)
        self.atomic_table.resizeColumnsToContents()
        metrics = self.atomic_table.fontMetrics()
        self.atomic_table.setColumnWidth(
            0, max(self.atomic_table.columnWidth(0), metrics.horizontalAdvance("000000") + 12)
        )
        self.atomic_table.setColumnWidth(
            1, max(self.atomic_table.columnWidth(1), metrics.horizontalAdvance("类型列") + 12)
        )
        operation_width = max(
            [
                self.atomic_table.cellWidget(row, 4).sizeHint().width()
                for row in range(self.atomic_table.rowCount())
            ]
            or [2 * 64 + 6 + 12]
        )
        self.atomic_table.setColumnWidth(4, operation_width)

    def show_atomic_detail(self, item):
        dialog = QDialog(self)
        dialog.setWindowTitle(f"原子记忆详情 · {item['id']}")
        dialog.resize(620, 420)
        _apply_memory_dialog_surface(dialog, self._colors)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        for key, value in (
            ("ID", item.get("id")),
            ("类型", item.get("type") or item.get("kind")),
            ("关键词组", ", ".join(item.get("keywords") or [])),
            ("文件链接", item.get("file_url") or "—"),
            ("时间", item.get("timestamp") or "—"),
            ("调取次数", item.get("retrieval_count", 0)),
            ("版本", item.get("version", 1)),
            ("上一版本", item.get("previous_id") or "—"),
        ):
            value_label = QLabel(str(value))
            value_label.setWordWrap(True)
            form.addRow(QLabel(f"{key}："), value_label)
        body = QTextEdit()
        body.setReadOnly(True)
        body.setPlainText(item["body"])
        apply_textedit(body, self._colors)
        form.addRow(QLabel("完整记忆："), body)
        layout.addLayout(form)
        close = QPushButton("关闭")
        apply_secondary_btn(close, self._colors)
        close.setFixedWidth(72)
        close.clicked.connect(dialog.accept)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        dialog.exec()

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
        self.refresh_atomic_memories(reset_page=False)

    def clear_atomic_memories(self):
        answer = QMessageBox.question(self, "清空原子记忆", "确定删除全部原子记忆吗？此操作不可恢复。",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.store.clear_atomic()
            self.refresh_atomic_memories()

    def open_proposal(self, proposal):
        dialog = MemoryProposalDialog(proposal, self.store, self, colors=self._colors)
        dialog.finished.connect(lambda _: self.refresh())
        dialog.show()

    def decide_proposal(self, ident, *, accept):
        try:
            self.store.decide(ident, accept=accept)
        except (ValueError, OSError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "无法处理记忆更新", str(exc))
            return
        self.refresh()

    def save_thresholds(self):
        try:
            self.store.set_thresholds(self.trigger.value() / 100, self.target.value() / 100)
        except ValueError as exc:
            QMessageBox.warning(self, "无法保存", str(exc))

    def save_global_memory(self):
        try:
            changed = self.store.save_global(
                _parse_global_memory_text(self.content.toPlainText())
            )
        except (ValueError, OSError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "无法保存全局记忆", str(exc))
            return
        self.refresh()
        QMessageBox.information(self, "已保存", "全局记忆已保存。" if changed else "内容未变化。")

    def reset_global_memory(self):
        answer = QMessageBox.question(
            self,
            "一键重置全局记忆",
            "确定恢复初始模板吗？用户画像和自定义全局规则会被清空，待处理更新会被废弃。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            environment = self._settings_environment()
            if not environment:
                environment = self.store.global_memory()["content"].get("环境信息", "")
            changed = self.store.reset_global_memory(environment)
        except (ValueError, OSError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "无法重置全局记忆", str(exc))
            return
        self.refresh()
        QMessageBox.information(
            self,
            "已重置",
            "全局记忆已恢复初始模板。" if changed else "全局记忆已经是初始模板。",
        )


class GlobalMemoryWorkspace(MemoryWorkspace):
    def __init__(self, theme, parent=None, *, store=None):
        super().__init__(theme, parent, store=store)
        self.atomic_card.setVisible(False)


class AtomicMemoryWorkspace(MemoryWorkspace):
    def __init__(self, theme, parent=None, *, store=None):
        super().__init__(theme, parent, store=store)
        self.memory_title.hide()
        self.threshold_row.hide()
        self.refresh_row.hide()
        self.pending_scroll.hide()
        self.global_card.setVisible(False)
        self.trigger.setVisible(False)
        self.target.setVisible(False)

