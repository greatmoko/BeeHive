"""任务运行历史展示与详情入口。"""
from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout, QListWidget, QListWidgetItem
from beehive.features.autobee.core.store import MAX_LOGS_PER_TASK
from beehive.features.autobee.ui.autobee_log_view import _LogDetailDialog, _LogRow

class TaskHistory(QWidget):
    def __init__(self, theme, store, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.store = store
        self.task_id = None
        self._log_cache = {}
        c = theme.colors
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        # ⑥ 运行历史（接在推送地址后，随表单滚动；默认最近 10 条）
        hist_lab = QLabel(f"运行历史（最近 {MAX_LOGS_PER_TASK} 条）")
        hist_lab.setStyleSheet(
            f"font-size: 12px; color: {c['text_secondary']};"
            "background: transparent; border: none; font-weight: bold;"
        )
        layout.addWidget(hist_lab)
        self._logs = QListWidget()
        self._logs.setMinimumHeight(120)
        self._logs.setMaximumHeight(220)
        self._logs.setCursor(Qt.CursorShape.PointingHandCursor)
        self._logs.setStyleSheet(f"""
            QListWidget {{
                background: {c["input_bg"]}; border: 1px solid {c["input_border"]};
                border-radius: 8px; padding: 4px; outline: none;
            }}
            QListWidget::item {{
                border: none; border-bottom: 1px solid {c["border_light"]};
                border-radius: 4px; margin: 1px 0;
                min-height: 40px;
            }}
            QListWidget::item:selected {{
                background: {c["subnav_active"]};
            }}
            QListWidget::item:hover {{
                background: {c["subnav_hover"]};
            }}
        """)
        self._logs.itemClicked.connect(self._on_log_clicked)
        layout.addWidget(self._logs)

    def _load_logs(self, task_id: str):
        self._logs.clear()
        self._log_cache.clear()
        logs = self.store.list_logs(task_id, limit=MAX_LOGS_PER_TASK)
        # 新的在前
        logs = list(reversed(logs))
        if not logs:
            empty = QListWidgetItem("暂无运行历史")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self._logs.addItem(empty)
            return
        task = self.store.get(task_id)
        task_name = (task.name if task else "") or "—"
        for log in logs:
            self._log_cache[log.id] = log
            row = _LogRow(self.theme, log, task_name)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, log.id)
            item.setSizeHint(row.sizeHint())
            self._logs.addItem(item)
            self._logs.setItemWidget(item, row)

    def _on_log_clicked(self, item: QListWidgetItem):
        log_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not log_id:
            return
        log = self._log_cache.get(str(log_id))
        if not log:
            return
        task = self.store.get(self.task_id or "") if self.task_id else None
        name = (task.name if task else "") or ""
        _LogDetailDialog(self.theme, log, name, self).exec()

    def load(self, task_id):
        self.task_id = task_id
        self._load_logs(task_id)

    def clear(self):
        self.task_id = None
        self._log_cache.clear()
        self._logs.clear()
        empty = QListWidgetItem("暂无运行历史")
        empty.setFlags(Qt.ItemFlag.NoItemFlags)
        self._logs.addItem(empty)
