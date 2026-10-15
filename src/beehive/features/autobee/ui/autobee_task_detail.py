"""任务详情装配与调度事件协调。"""
from __future__ import annotations
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout, QStackedWidget
from beehive.common.ui.theme import Theme
from beehive.system.providers.provider_store import ProviderStore
from beehive.features.wokbee.projects.project_store import ProjectStore
from beehive.features.wokbee.gateway.manager import GatewayManager
from beehive.features.autobee.core.store import AutoBeeStore
from beehive.features.autobee.engine.scheduler import SchedulerService
from beehive.features.autobee.ui.task_actions import TaskActions
from beehive.features.autobee.ui.task_generation import TaskGenerationController
from beehive.features.autobee.ui.task_history import TaskHistory
from beehive.features.autobee.ui.task_form import TaskForm

class _TaskDetail(QWidget):
    task_saved = Signal(str)
    task_deleted = Signal(str)

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
        self._task_id: str | None = None
        self.actions = TaskActions(self)
        self.generation = TaskGenerationController(self)
        self.history = TaskHistory(theme, store, self)
        self._build()
        self.actions._update_action_btns()
        self.scheduler.notifier.task_started.connect(self._on_task_run_started)
        self.scheduler.notifier.task_progress.connect(self._on_task_run_progress)
        self.scheduler.notifier.task_finished.connect(self._on_task_run_finished)

    def _build(self):
        # 空态占位页 + 编辑表单页，未选中任务时展示空态
        self._empty = self._build_empty_page()
        self._form = TaskForm(self)
        self._stack = QStackedWidget()
        self._stack.addWidget(self._empty)
        self._stack.addWidget(self._form)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._stack)

    def _build_empty_page(self) -> QWidget:
        c = self.theme.colors
        page = QWidget()
        page.setStyleSheet(f"background: {c['content_bg']};")
        lay = QVBoxLayout(page)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = QLabel("⏰")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(f"font-size: 40px; color: {c['text_hint']}; background: transparent;")
        lay.addWidget(icon)
        t = QLabel("选择一个任务，或点击「新建任务」开始")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t.setStyleSheet(f"font-size: 14px; color: {c['text_hint']}; background: transparent; margin-top: 8px;")
        lay.addWidget(t)
        return page

    def set_empty(self):
        self._task_id = None
        self._stack.setCurrentWidget(self._empty)

    def prepare_new(self):
        self._task_id = None
        self._form._clear_form()
        self.history.clear()
        self._stack.setCurrentWidget(self._form)
        self._form._set_edit_enabled(True)
        self._form._name.setPlaceholderText("新任务名称")
        self._form._name.setFocus()
        self.actions._update_action_btns()

    def load(self, task):
        self._task_id = task.id
        self._stack.setCurrentWidget(self._form)
        self._form.load(task)
        self.history.load(task.id)

    @property
    def form(self):
        return self._form

    def refresh_logs(self):
        if self._task_id:
            self.history.load(self._task_id)

    def shutdown(self):
        return self.generation.shutdown()

    def _on_task_run_started(self, task_id: str):
        if self._task_id == task_id:
            self.actions._update_action_btns()
            self.refresh_logs()

    def _on_task_run_progress(self, task_id: str, message: str):
        if self._task_id != task_id:
            return
        self.actions._update_action_btns()
        self.refresh_logs()

    def _on_task_run_finished(self, task_id: str, _status: str, _message: str):
        if self._task_id == task_id:
            self.actions._update_action_btns()
            self.refresh_logs()
