"""AutoBee 任务列表卡片."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QVBoxLayout, QWidget, QSizePolicy,
)

from tokbee.ui.styles.theme import Theme

from autobee.core.models import ScheduledTask, TaskRunStatus
from autobee.engine.scheduler import describe_cron
from autobee.ui.autobee_ui_common import _STATUS_COLOR, _status_label

class _TaskItem(QFrame):
    clicked = Signal(str)
    toggle_enabled = Signal(str)

    def __init__(self, task: ScheduledTask, theme: Theme, selected: bool = False, parent=None):
        super().__init__(parent)
        self.task = task
        self.theme = theme
        self._selected = selected
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(66)
        self._build()

    def _build(self):
        c = self.theme.colors
        t = self.task
        bg = c["card_bg"] if self._selected else "transparent"
        border = (
            f"border-left: 3px solid {c['accent']};"
            if self._selected
            else "border-left: 3px solid transparent;"
        )
        self.setStyleSheet(f"""
            _TaskItem {{ background: {bg}; border-radius: 6px; {border} }}
            _TaskItem:hover {{ background: {c["subnav_hover"]}; }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)

        top = QHBoxLayout()
        status = TaskRunStatus(t.last_status) if t.last_status else None
        color = c.get(_STATUS_COLOR.get(status, "text_hint"), c["text_hint"])
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {color}; font-size: 10px; background: transparent; border: none;")
        self._status_dot = dot
        top.addWidget(dot)
        name = QLabel(t.name)
        name.setWordWrap(False)
        name.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        name.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {c['text']};"
            "background: transparent; border: none;"
        )
        top.addWidget(name, 1)

        # 列表内启用/停用开关
        toggle = QPushButton("停用" if t.enabled else "启用")
        toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        toggle.setFixedSize(44, 22)
        toggle.setToolTip("停用定时" if t.enabled else "启用定时")
        if t.enabled:
            toggle.setStyleSheet(f"""
                QPushButton {{
                    background: {c["btn_bg"]}; color: {c["text_secondary"]};
                    border: none; border-radius: 4px; font-size: 11px;
                }}
                QPushButton:hover {{ background: {c["btn_hover"]}; color: {c["danger"]}; }}
            """)
        else:
            toggle.setStyleSheet(f"""
                QPushButton {{
                    background: {c.get("accent_light", c["btn_bg"])}; color: {c["accent"]};
                    border: none; border-radius: 4px; font-size: 11px;
                }}
                QPushButton:hover {{ background: {c["btn_hover"]}; }}
            """)
        toggle.clicked.connect(lambda: self.toggle_enabled.emit(self.task.id))
        top.addWidget(toggle)
        layout.addLayout(top)

        mid = QHBoxLayout()
        chip = QLabel(t.task_type.label)
        chip.setStyleSheet(
            f"background: {c['tag_bg']}; color: {c['text_secondary']};"
            "border-radius: 4px; padding: 1px 6px; font-size: 10px;"
        )
        mid.addWidget(chip)
        sub = QLabel(describe_cron(t.schedule) or t.schedule)
        sub.setStyleSheet(
            f"font-size: 11px; color: {c['text_hint']}; background: transparent; border: none;"
        )
        mid.addWidget(sub)
        mid.addStretch()
        layout.addLayout(mid)

        enabled = "已启用" if t.enabled else "已停用"
        state = c["success"] if t.enabled else c["text_hint"]
        if t.last_status == TaskRunStatus.RUNNING.value:
            state = c["accent"]
        last_label = _status_label(t.last_status)
        info = QLabel(f"{enabled} · {last_label}")
        info.setStyleSheet(
            f"font-size: 11px; color: {state}; background: transparent; border: none;"
        )
        self._status_info = info
        layout.addWidget(info)

    def update_status(self, task: ScheduledTask):
        """只更新运行状态，保留任务行控件，避免列表闪烁。"""
        self.task = task
        status = TaskRunStatus(task.last_status) if task.last_status else None
        color = self.theme.colors.get(
            _STATUS_COLOR.get(status, "text_hint"), self.theme.colors["text_hint"]
        )
        self._status_dot.setStyleSheet(
            f"color: {color}; font-size: 10px; background: transparent; border: none;"
        )
        state = self.theme.colors["success"] if task.enabled else self.theme.colors["text_hint"]
        if task.last_status == TaskRunStatus.RUNNING.value:
            state = self.theme.colors["accent"]
        self._status_info.setText(
            f"{'已启用' if task.enabled else '已停用'} · {_status_label(task.last_status)}"
        )
        self._status_info.setStyleSheet(
            f"font-size: 11px; color: {state}; background: transparent; border: none;"
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.task.id)
        super().mousePressEvent(event)


class _TaskList(QFrame):
    task_selected = Signal(str)
    new_task = Signal()
    toggle_enabled = Signal(str)

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._filter_keys: list[str] = []
        self._selected_id: str | None = None
        self._build()

    def _build(self):
        c = self.theme.colors
        self.setMinimumWidth(250)
        self.setMaximumWidth(340)
        self.setStyleSheet(f"""
            _TaskList {{ background: {c["content_bg"]}; border-right: 1px solid {c["border"]}; }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        new_btn = QPushButton("＋ 新建任务")
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setFixedHeight(34)
        new_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c["btn_primary"]}; color: #ffffff;
                border: none; border-radius: 6px; font-size: 13px;
            }}
            QPushButton:hover {{ background: {c["btn_primary_hover"]}; }}
        """)
        new_btn.clicked.connect(self.new_task.emit)
        layout.addWidget(new_btn)

        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索任务...")
        self._search.setFixedHeight(30)
        self._search.textChanged.connect(lambda: self.refresh())
        self._search.setStyleSheet(f"""
            QLineEdit {{
                background: {c["input_bg"]}; color: {c["text"]};
                border: 1px solid {c["input_border"]}; border-radius: 6px; padding: 0 8px;
            }}
        """)
        layout.addWidget(self._search)

        self._count = QLabel("")
        self._count.setStyleSheet(
            f"font-size: 11px; color: {c['text_hint']}; background: transparent; border: none;"
        )
        layout.addWidget(self._count)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._container = QWidget()
        self._container.setStyleSheet("background: transparent;")
        self._list_layout = QVBoxLayout(self._container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(2)
        self._list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self._container)
        layout.addWidget(scroll, stretch=1)

    def set_filter(self, keys: list[str]):
        self._filter_keys = list(keys or [])
        self.refresh()

    def set_tasks(self, tasks: list[ScheduledTask]):
        self._tasks = list(tasks)
        self.refresh()

    def update_task_status(self, task: ScheduledTask):
        """更新单个任务项，不重建任务列表。"""
        for index in range(self._list_layout.count()):
            item = self._list_layout.itemAt(index)
            widget = item.widget()
            if isinstance(widget, _TaskItem) and widget.task.id == task.id:
                widget.update_status(task)
                break
        for index, current in enumerate(getattr(self, "_tasks", [])):
            if current.id == task.id:
                self._tasks[index] = task
                break

    def refresh(self):
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        c = self.theme.colors
        kw = (self._search.text() or "").strip().lower()
        tasks = getattr(self, "_tasks", [])
        filtered = []
        for t in tasks:
            if kw and kw not in t.name.lower() and kw not in t.schedule.lower():
                continue
            if self._filter_keys:
                if "__enabled" in self._filter_keys and not t.enabled:
                    continue
                if "__disabled" in self._filter_keys and t.enabled:
                    continue
                if t.task_type.value not in self._filter_keys:
                    continue
            filtered.append(t)

        self._count.setText(f"{len(filtered)} 个任务")
        if not filtered:
            empty = QLabel("暂无任务")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(f"font-size: 12px; color: {c['text_hint']}; padding: 20px 0;")
            self._list_layout.addWidget(empty)
            self._selected_id = None
            return

        found_selected = False
        for t in filtered:
            if t.id == self._selected_id:
                found_selected = True
            item = _TaskItem(t, self.theme, selected=(t.id == self._selected_id))
            item.clicked.connect(self._on_select)
            item.toggle_enabled.connect(self.toggle_enabled.emit)
            self._list_layout.addWidget(item)

        # 若当前选中的任务不在过滤队列内，清空选中（详情保持上次编辑不丢）
        if self._selected_id and not found_selected:
            self._selected_id = None

    def _on_select(self, task_id: str):
        self._selected_id = task_id
        self.refresh()
        self.task_selected.emit(task_id)

    def select(self, task_id: str):
        self._selected_id = task_id
        self.refresh()
        self.task_selected.emit(task_id)

    def clear_selection(self):
        self._selected_id = None
        self.refresh()

    def current_selected(self) -> str | None:
        return self._selected_id




__all__ = ["_TaskItem", "_TaskList"]
