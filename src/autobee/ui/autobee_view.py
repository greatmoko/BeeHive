"""AutoBee 主视图：中任务列表 | 右任务详情。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QTimer, QThread, QSize
from PySide6.QtWidgets import (
    QWidget, QFrame, QLabel, QVBoxLayout, QHBoxLayout, QLineEdit,
    QTextEdit, QComboBox, QScrollArea, QPushButton, QStackedWidget,
    QSizePolicy, QListWidget, QListWidgetItem, QDialog, QMessageBox, QCheckBox,
)

from apscheduler.triggers.cron import CronTrigger

from tokbee.ui.styles.theme import Theme
from tokbee.ui.combo_style import apply_combo_popup_style, secondary_btn_qss
from tokbee.core.provider_store import ProviderStore

from wokbee.core.project_store import ProjectStore
from wokbee.gateway.manager import GatewayManager

from autobee.core.models import JobLog, ScheduledTask, TaskRunStatus, TaskType, new_task_id
from autobee.core.store import AutoBeeStore, MAX_LOGS_PER_TASK
from autobee.engine.nl_builder import NLBuilder
from autobee.engine.scheduler import SchedulerService, describe_cron
from autobee.ui.autobee_task_detail import _TaskDetail
from autobee.ui.autobee_task_list import _TaskList

class AutoBeeView(QWidget):
    """AutoBee 模块容器：三栏布局 + 2s 轮询刷新。"""

    def __init__(self, theme: Theme, store: AutoBeeStore, scheduler: SchedulerService,
                 provider_store: ProviderStore, project_store: ProjectStore,
                 gateway_manager: GatewayManager | None = None, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.store = store
        self.scheduler = scheduler
        self.provider_store = provider_store
        self.project_store = project_store
        self.gateway_manager = gateway_manager
        self._tasks: list[ScheduledTask] = []

        self._build()
        self._refresh_tasks()
        if self.gateway_manager:
            self.gateway_manager.notifier.status_changed.connect(
                self._on_gateway_status_changed
            )
        self.scheduler.notifier.task_started.connect(self._on_scheduler_task_started)
        self.scheduler.notifier.task_progress.connect(self._on_scheduler_task_progress)
        self.scheduler.notifier.task_finished.connect(self._on_scheduler_task_finished)
        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

    def _on_gateway_status_changed(self, channel: str, _status: str):
        if channel == "wechat":
            self.detail._update_wechat_notify_state()

    def _build(self):
        self.setStyleSheet(f"background: {self.theme.colors['content_bg']};")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 中间栏：搜索 + 新建 + 任务列表
        self.task_list = _TaskList(self.theme)
        self.task_list.new_task.connect(self._on_new)
        self.task_list.task_selected.connect(self._on_task_selected)
        self.task_list.toggle_enabled.connect(self._on_list_toggle)
        layout.addWidget(self.task_list)

        # 右栏：任务详情
        self.detail = _TaskDetail(
            self.theme, self.store, self.scheduler, self.provider_store, self.project_store,
            gateway_manager=self.gateway_manager,
        )
        self.detail.task_saved.connect(self._on_saved)
        self.detail.task_deleted.connect(self._on_deleted)
        layout.addWidget(self.detail, stretch=1)

    # ── 数据流 ─────────────────────────────────────────────
    def _refresh_tasks(self):
        self._tasks = self.store.list_tasks()
        self.task_list.set_tasks(self._tasks)

    def _on_new(self):
        self._refresh_tasks()
        self.task_list.clear_selection()
        self.detail.prepare_new()

    def _on_task_selected(self, task_id: str):
        task = self.store.get(task_id)
        if task:
            self.detail.load(task)

    def _on_saved(self, task_id: str):
        self._refresh_tasks()
        self.task_list.select(task_id)

    def _on_deleted(self, task_id: str):
        self._refresh_tasks()
        if self._tasks:
            self.task_list.select(self._tasks[0].id)
        else:
            self.detail.set_empty()

    def _on_state_changed(self, task_id: str):
        self._refresh_tasks()
        task = self.store.get(task_id)
        if task:
            self.task_list.select(task_id)

    def _on_list_toggle(self, task_id: str):
        """左侧列表启用/停用。"""
        task = self.store.get(task_id)
        if not task:
            return
        if task.enabled:
            self.scheduler.pause(task_id)
        else:
            self.scheduler.resume(task_id)
        self._on_state_changed(task_id)

    def _on_scheduler_task_started(self, task_id: str):
        self._refresh_tasks()
        if self.task_list.current_selected() == task_id:
            self.detail._update_action_btns()
            self.detail.refresh_logs()

    def _on_scheduler_task_progress(self, task_id: str, _message: str):
        task = self.store.get(task_id)
        if task:
            self.task_list.update_task_status(task)

    def _on_scheduler_task_finished(self, task_id: str, _status: str, _message: str):
        self._refresh_tasks()
        if self.task_list.current_selected() == task_id:
            self.detail._update_action_btns()
            self.detail.refresh_logs()

    def _on_tick(self):
        # 刷新所选任务的下一步运行时间与日志
        curr = self.task_list.current_selected()
        if curr:
            task = self.store.get(curr)
            if task and self.scheduler.running:
                task.next_run = self.scheduler.next_run_time(curr)
            self.detail.refresh_logs()

    def shutdown(self):
        if getattr(self, "_timer", None):
            self._timer.stop()
