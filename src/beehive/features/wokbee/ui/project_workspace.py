"""项目工作区装配、当前项目及后台线程生命周期。"""
from __future__ import annotations
from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget
from beehive.common.ui.theme import Theme
from beehive.features.wokbee.projects.models import ProjectStatus
from beehive.features.wokbee.projects.project_store import ProjectStore
from beehive.features.wokbee.ui.action_bar import _ActionBar
from beehive.features.wokbee.ui.timeline_events import INITIAL_RENDER
from beehive.features.wokbee.ui.web_chat import _WebChat
from beehive.features.wokbee.ui.project_metadata import _ProjectEssentials
from beehive.features.wokbee.ui.project_workers import _CompactWorker, _RefineMetaWorker
from beehive.features.wokbee.ui.project_context import ProjectContextController
from beehive.features.wokbee.ui.project_meta_actions import ProjectMetadataController
from beehive.features.wokbee.ui.project_execution import ProjectExecutionController
from beehive.features.wokbee.ui.project_events import ProjectEventsController
from beehive.features.wokbee.ui.project_approval import ProjectApprovalController
from beehive.features.wokbee.ui.project_files import ProjectFilesController
from beehive.features.wokbee.ui.project_lessons import ProjectLessonsController

class _ProjectWorkspace(QWidget):
    status_changed = Signal()

    def __init__(self, theme: Theme, store: ProjectStore, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.store = store
        self._project_id: str | None = None
        self._worker: AgentWorker | None = None
        self._lesson_worker: LessonWorker | None = None
        # 运行/对话/经验 worker 的事件应落到的项目（发起时捕获），避免运行中切换项目串写
        self._worker_project_id: str | None = None
        self._compact_project_id: str | None = None
        self._refine_project_id: str | None = None
        self._worker_mode: str = "run"  # 运行、对话或经验生成
        self._status_before_chat: ProjectStatus | None = None
        self._status_before_lesson: ProjectStatus | None = None
        self._compact_worker: _CompactWorker | None = None
        self._refine_worker: _RefineMetaWorker | None = None
        self.context = ProjectContextController(self)
        self.metadata = ProjectMetadataController(self)
        self.execution = ProjectExecutionController(self)
        self.events = ProjectEventsController(self)
        self.approval = ProjectApprovalController(self)
        self.files = ProjectFilesController(self)
        self.lessons = ProjectLessonsController(self)
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._essentials = _ProjectEssentials(self.theme)
        self._essentials.goal_edit_requested.connect(self.metadata._edit_goal)
        self._essentials.approval_edit_requested.connect(self.approval._edit_approval)
        self._essentials.ai_refine_requested.connect(self.metadata._on_ai_refine_meta)
        layout.addWidget(self._essentials)

        self._timeline = _WebChat(self.theme)
        layout.addWidget(self._timeline, stretch=1)
        self._timeline.bridge().open_deliverables_requested.connect(self.files._on_open_deliverables)

        self._actions = _ActionBar(self.theme)
        self._actions.run_clicked.connect(self.execution._on_run)
        self._actions.pause_requested.connect(self.execution._on_pause)
        self._actions.open_folder_clicked.connect(self.files._on_open_folder)
        self._actions.upload_clicked.connect(self.files._on_upload)
        self._actions.archive_clicked.connect(self.files._on_archive)
        self._actions.send_clicked.connect(self.execution._on_send)
        self._actions.approve_clicked.connect(self.approval._on_approve)
        self._actions.reject_clicked.connect(self.approval._on_reject)
        self._actions.model_changed.connect(self.context._on_model_changed)
        self._actions.draft_changed.connect(self.context._schedule_usage_refresh)
        layout.addWidget(self._actions)

        self._essentials_timer = QTimer(self)
        self._essentials_timer.setSingleShot(True)
        self._essentials_timer.setInterval(200)
        self._essentials_timer.timeout.connect(self._refresh_essentials)
        self._sidebar_timer = QTimer(self)
        self._sidebar_timer.setSingleShot(True)
        self._sidebar_timer.setInterval(250)
        self._sidebar_timer.timeout.connect(self.status_changed.emit)
        self._usage_timer = QTimer(self)
        self._usage_timer.setSingleShot(True)
        self._usage_timer.setInterval(400)
        self._usage_timer.timeout.connect(self.context._refresh_context_usage)
        self.show_welcome()

    def show_welcome(self):
        self._project_id = None
        self._essentials.clear()
        self._timeline.show_empty()
        self._actions.hide_approval()
        self._actions.set_running(False)
        self._actions.set_uploads_root(None)
        self._actions.reload_models(
            fallback_provider=self.store.settings.default_provider,
            fallback_model=self.store.settings.default_model_id,
        )
        self.context._refresh_context_usage()

    def load_project(self, project_id: str, *, force_timeline: bool = False):
        if not project_id:
            self.show_welcome()
            return
        project = self.store.get(project_id)
        if not project:
            self.show_welcome()
            return
        same = self._project_id == project_id
        self._project_id = project_id
        root = self.store.path_for(project_id)
        self._actions.set_uploads_root(root / "uploads")
        self._essentials.bind(project, project_root=root)
        # 切换项目：只加载最新一批，上翻时再按批前置，加快进入速度
        if not same:
            events, remaining = self.store.events_window(
                project_id, skip_from_end=0, count=INITIAL_RENDER
            )
            self._timeline.render_events(
                events, older_remaining=remaining,
                loader=self._make_events_loader(project_id),
            )
        elif force_timeline or not self._timeline._bubbles:
            events, remaining = self.store.events_window(
                project_id, skip_from_end=0, count=INITIAL_RENDER
            )
            self._timeline.render_events(
                events, older_remaining=remaining,
                loader=self._make_events_loader(project_id),
            )
        self._timeline.set_agent_running(
            self._worker is not None and self._worker.isRunning()
        )
        self._actions.reload_models(
            project.provider,
            project.model_id,
            fallback_provider=self.store.settings.default_provider,
            fallback_model=self.store.settings.default_model_id,
        )
        # 项目尚未绑定模型时，把当前下拉选择写回，保证运行用同一模型
        if not (project.provider and project.model_id):
            p, m = self._actions.selected_model()
            if m:
                project.provider = p
                project.model_id = m
                self.store.save(project)
        self.context._refresh_context_usage()

    def _make_events_loader(self, project_id: str):
        """生成时间线「加载更早记录」闭包：从项目 events 文件尾部往前取一批。"""
        def _load(skip_from_end: int, count: int):
            events, remaining = self.store.events_window(
                project_id, skip_from_end=skip_from_end, count=count
            )
            return events, remaining
        return _load

    def _refresh(self):
        """完整刷新（切换项目、归档、手动操作后）。运行中请勿频繁调用。"""
        if self._project_id:
            self.load_project(self._project_id, force_timeline=True)

    def _refresh_essentials(self):
        if not self._project_id:
            return
        project = self.store.get(self._project_id)
        if project:
            self._essentials.bind(
                project, project_root=self.store.path_for(self._project_id)
            )

    def _schedule_essentials_refresh(self):
        self._essentials_timer.start()
        # 同步刷新左侧项目列表状态（防抖）
        self._sidebar_timer.start()

    def _active_project(self) -> tuple[str | None, bool]:
        """返回当前回调应写到的 (项目 id, 该项目是否正被查看)。

        运行中 worker / 压缩 / 改名回调都绑定其**发起时**的项目；若用户已切到别的项目，
        仍把事件/状态写回发起项目（保全数据），但不再污染当前可见时间线。
        """
        target = self._worker_project_id or self._project_id
        return target, target == self._project_id

    def shutdown(self):
        """退出前收尾：取消并等待所有在途 worker。

        避免应用关闭时 QThread 仍在运行导致「QThread: Destroyed while thread is
        still running」或行为未定义。逐类取消后 wait() 兜底；给足超时以便真取消。
        """
        workers = (
            self._worker,
            self._lesson_worker,
            self._compact_worker,
            self._refine_worker,
        )
        for wk in workers:
            if wk is not None and wk.isRunning():
                try:
                    wk.cancel()
                except Exception:
                    pass
        for wk in workers:
            if wk is not None and wk.isRunning():
                try:
                    wk.wait(5000)
                except Exception:
                    pass
