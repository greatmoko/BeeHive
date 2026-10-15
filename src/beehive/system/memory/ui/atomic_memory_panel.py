"""原子记忆查询、分页、详情与删除，分页状态只由该控件持有。"""
import sqlite3
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFormLayout, QHBoxLayout, QLabel, QPushButton, QHeaderView, QLineEdit, QSpinBox, QTextEdit, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget
from beehive.common.ui.dialogs.message import show_tip as _tip, _confirm
from beehive.common.ui.styles.buttons import apply_danger_btn, apply_secondary_btn
from beehive.common.ui.styles.inputs import apply_lineedit, apply_spin, apply_textedit
from beehive.system.memory.ui.memory_document import _apply_memory_dialog_surface


class AtomicMemoryPanel(QWidget):
    def __init__(self, colors, store, parent=None):
        super().__init__(parent)
        self._colors, self.store = colors, store
        self._theme = SimpleNamespace(colors=colors)
        self._atomic_rows = []
        self._atomic_page = 1
        self._atomic_total_pages = 1
        atomic_card = self
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
        if not _confirm(self, self._theme, "删除原子记忆", "确定删除这条原子记忆吗？此操作不可恢复。"):
            return
        try:
            self.store.delete(ident)
        except (ValueError, OSError, sqlite3.Error) as exc:
            _tip(self, self._theme, str(exc), title="无法删除")
            return
        self.refresh_atomic_memories(reset_page=False)

    def clear_atomic_memories(self):
        if not _confirm(self, self._theme, "清空原子记忆", "确定删除全部原子记忆吗？此操作不可恢复。"):
            return
        self.store.clear_atomic()
        self.refresh_atomic_memories()
