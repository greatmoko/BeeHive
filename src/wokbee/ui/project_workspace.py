"""WokBee 工作区：侧栏项目列表、项目要素、后台 worker、与三段式整体工作区。

聚合了四种自有定义：侧栏/项目列表、项目要素、后台 worker（上下文压缩 / AI 改名）、
以及把时间线 + 操作栏 + 侧栏拼成一体的 `_ProjectWorkspace`。时间线与操作栏本身
分别位于 `timeline.py` / `action_bar.py`。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from tokbee.core import context_manager as ctxman
from tokbee.core.ai_client import AIClient
from tokbee.core.provider_store import ProviderStore, ResolvedModel
from tokbee.core.session_settings import SessionSettings, ProviderOptions
from tokbee.ui.styles.system import make_context_menu
from tokbee.ui.styles.theme import Theme

from wokbee.core.context_usage import (
    estimate_project_usage,
    load_context_state,
    plan_project_compaction,
    save_context_state,
)
from wokbee.core.models import MAX_PROJECT_TITLE_LEN, Project, ProjectEvent, ProjectStatus
from wokbee.core.paths import deliverables_dir, list_deliverable_names, uploads_dir
from wokbee.core.project_store import MAX_ARCHIVES, ProjectStore, TRASH_RETENTION_DAYS
from wokbee.ui.action_bar import _ActionBar
from wokbee.ui.ask_user_dialog import AskUserDialog
from wokbee.ui.dialogs import (
    _ask_multiline,
    _ask_text,
    _confirm,
    _default_project_title,
    _prompt_approval_flags,
    open_path as _open_in_explorer,
    tip as _tip,
)
from wokbee.ui.timeline import INITIAL_RENDER
from wokbee.ui.web_chat import _WebChat
from wokbee.ui.project_metadata import _CompactWorker, _ProjectEssentials, _RefineMetaWorker
from wokbee.ui.project_navigation import _ProjectItem, _ProjectSidebar


logger = logging.getLogger("wokbee")


def _model_request_settings(model: ResolvedModel) -> SessionSettings:
    """将厂商-模型配置转换为 TokBee 客户端请求参数。"""
    return SessionSettings(
        temperature=model.temperature,
        top_p=model.top_p,
        max_tokens=model.max_tokens,
        stream=model.stream,
        provider_options=ProviderOptions(
            reasoning_adapter=model.reasoning_adapter,
            reasoning_effort=model.reasoning_effort,
            reasoning_enabled=model.reasoning_enabled,
        ),
    )


STATUS_LABEL = {
    ProjectStatus.IDLE: "空闲",
    ProjectStatus.RUNNING: "运行中",
    ProjectStatus.AWAITING_APPROVAL: "待审批",
    ProjectStatus.FAILED: "失败",
    ProjectStatus.DONE: "完成",
}

STATUS_COLOR_KEY = {
    ProjectStatus.IDLE: "text_hint",
    ProjectStatus.RUNNING: "accent",
    ProjectStatus.AWAITING_APPROVAL: "warning",
    ProjectStatus.FAILED: "danger",
    ProjectStatus.DONE: "success",
}

# 新建项目时，名称默认取目标前 N 个字（也是名称硬上限）
TITLE_FROM_GOAL_LEN = MAX_PROJECT_TITLE_LEN


# ─── 列表项 ───


# ─── 工作区整体 ───

class _ProjectWorkspace(QWidget):
    status_changed = Signal()  # 通知侧栏同步项目状态

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
        self._worker_mode: str = "run"  # run | chat | lesson
        self._status_before_chat: ProjectStatus | None = None
        self._status_before_lesson: ProjectStatus | None = None
        self._compact_worker: _CompactWorker | None = None
        self._refine_worker: _RefineMetaWorker | None = None
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._essentials = _ProjectEssentials(self.theme)
        self._essentials.goal_edit_requested.connect(self._edit_goal)
        self._essentials.approval_edit_requested.connect(self._edit_approval)
        self._essentials.ai_refine_requested.connect(self._on_ai_refine_meta)
        layout.addWidget(self._essentials)

        self._timeline = _WebChat(self.theme)
        layout.addWidget(self._timeline, stretch=1)
        self._timeline.bridge().open_deliverables_requested.connect(self._on_open_deliverables)

        self._actions = _ActionBar(self.theme)
        self._actions.run_clicked.connect(self._on_run)
        self._actions.pause_requested.connect(self._on_pause)
        self._actions.open_folder_clicked.connect(self._on_open_folder)
        self._actions.upload_clicked.connect(self._on_upload)
        self._actions.archive_clicked.connect(self._on_archive)
        self._actions.send_clicked.connect(self._on_send)
        self._actions.approve_clicked.connect(self._on_approve)
        self._actions.reject_clicked.connect(self._on_reject)
        self._actions.model_changed.connect(self._on_model_changed)
        self._actions.compress_clicked.connect(self._on_compress_clicked)
        self._actions.draft_changed.connect(self._schedule_usage_refresh)
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
        self._usage_timer.timeout.connect(self._refresh_context_usage)
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
        self._refresh_context_usage()

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
        self._refresh_context_usage()

    def _make_events_loader(self, project_id: str):
        """生成时间线「加载更早记录」闭包：从项目 events 文件尾部往前取一批。"""
        def _load(skip_from_end: int, count: int):
            events, remaining = self.store.events_window(
                project_id, skip_from_end=skip_from_end, count=count
            )
            return events, remaining
        return _load

    def _on_model_changed(self, provider_id: str, model_id: str):
        if not self._project_id:
            # 无项目时：同步到厂商「默认模型」，避免写进 WokBee 旧配置覆盖厂商默认
            if provider_id and model_id:
                try:
                    ProviderStore().set_default_model(provider_id, model_id)
                except Exception:
                    pass
            return
        project = self.store.get(self._project_id)
        if not project:
            return
        if project.provider == provider_id and project.model_id == model_id:
            self._refresh_context_usage()
            return
        project.provider = provider_id or ""
        project.model_id = model_id or ""
        self.store.save(project)
        # 气泡用厂商显示名，与下拉框一致（勿用 provider_id）
        display = (self._actions._model_combo.currentText() or "").strip()
        if not display or display.startswith("未配置"):
            try:
                resolved = ProviderStore().resolve(provider_id, model_id)
                if resolved:
                    display = f"{resolved.provider_name} / {resolved.model_id}"
            except Exception:
                display = ""
        if not display:
            display = f"{provider_id}/{model_id}"
        ev = ProjectEvent(
            kind="info",
            content=f"已切换模型：{display}",
            meta={"provider": provider_id, "model_id": model_id, "display": display},
        )
        self.store.append_event(self._project_id, ev)
        self._timeline.append_event(ev)
        self._refresh_context_usage()

    def _schedule_usage_refresh(self):
        self._usage_timer.start()

    def _context_window_for_current(self) -> int:
        provider_id, model_id = self._actions.selected_model()
        if not model_id:
            return 0
        try:
            resolved = ProviderStore().resolve(provider_id, model_id)
            if resolved:
                return int(getattr(resolved, "context_window", 0) or 0)
        except Exception:
            pass
        return 0

    def _refresh_context_usage(self):
        if not self._project_id:
            self._actions.set_context_usage(0, 0, enabled=False)
            return
        root = self.store.path_for(self._project_id)
        events, _remaining = self.store.events_window(
            self._project_id, skip_from_end=0, count=500
        )
        usage = estimate_project_usage(
            events=events,
            project_root=root,
            context_window=self._context_window_for_current(),
            draft_text=self._actions.draft_text(),
        )
        busy = bool(self._compact_worker and self._compact_worker.isRunning())
        self._actions.set_context_usage(
            usage.used, usage.limit, enabled=not busy,
        )

    def _on_compress_clicked(self):
        if not self._project_id:
            _tip(self, self.theme, "请先新建或选择一个项目。")
            return
        if self._compact_worker and self._compact_worker.isRunning():
            return
        if self._worker and self._worker.isRunning():
            _tip(self, self.theme, "Agent 运行中，请稍后再压缩。")
            return
        root = self.store.path_for(self._project_id)
        events = self.store.list_events(self._project_id)
        plan = plan_project_compaction(events, root)
        if plan is None:
            _tip(self, self.theme, "当前上下文较短，无需压缩。")
            return

        to_compact, _retained, new_boundary, prev_summary, pin_end = plan
        client = None
        resolved = None
        provider_id, model_id = self._actions.selected_model()
        try:
            resolved = ProviderStore().resolve(provider_id, model_id)
            if resolved and resolved.api_host:
                client = AIClient(
                    resolved.api_host,
                    resolved.api_key,
                    resolved.model_id,
                    family=resolved.family,
                    protocol=resolved.api_protocol,
                )
        except Exception:
            client = None

        self._compact_project_id = self._project_id  # 发起时捕获，切换项目不写错
        worker = _CompactWorker(
            client,
            to_compact,
            prev_summary,
            new_boundary,
            parent=self,
            pin_end=pin_end,
            model=resolved,
        )
        self._compact_worker = worker
        worker.compact_done.connect(self._on_compact_done)
        worker.failed.connect(self._on_compact_failed)
        worker.start()
        self._refresh_context_usage()

    def _on_compact_done(self, summary: str, boundary: int, pin_end: int = 0):
        target = self._compact_project_id or self._project_id
        self._compact_worker = None
        self._compact_project_id = None
        if not target:
            return
        root = self.store.path_for(target)
        state = load_context_state(root)
        state["compaction_points"] = ctxman.append_compaction_point(
            state.get("compaction_points") or [],
            summary=summary,
            boundary_index=boundary,
            pin_end=pin_end,
        )
        save_context_state(root, state)
        self._refresh_context_usage()
        _tip(self, self.theme, "已压缩上下文（已钉住首条任务前缀）。")

    def _on_compact_failed(self, err: str):
        self._compact_worker = None
        self._compact_project_id = None
        self._refresh_context_usage()
        _tip(self, self.theme, f"压缩失败：{err}")

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

    def _edit_goal(self):
        if not self._project_id:
            return
        project = self.store.get(self._project_id)
        if not project:
            return
        new_goal = _ask_multiline(
            self,
            self.theme,
            "编辑目标",
            "项目目标（多行，超出可滚动）",
            project.goal,
            min_lines=5,
        )
        if new_goal is None:
            return
        self.store.update_goal(self._project_id, new_goal)
        self._refresh()

    def _on_ai_refine_meta(self):
        """一键调用 AI，根据当前目标与最近时间线更新名称+目标。"""
        if not self._project_id:
            return
        if self._refine_worker and self._refine_worker.isRunning():
            return
        if self._worker and self._worker.isRunning():
            _tip(self, self.theme, "请先等待当前运行/对话结束。")
            return
        if self._lesson_worker and self._lesson_worker.isRunning():
            _tip(self, self.theme, "正在总结经验，请稍候。")
            return
        project = self.store.get(self._project_id)
        if not project:
            return

        root = self.store.path_for(self._project_id)
        recent_events, _remaining = self.store.events_window(
            self._project_id, skip_from_end=0, count=20
        )
        context_lines = [
            f"[{ev.kind}] {(ev.content or '').strip()}"
            for ev in reversed(recent_events)
            if (ev.content or "").strip()
        ]
        recent_context = "\n".join(context_lines)[-8000:] or "（暂无最近交互记录）"
        self._essentials.set_ai_refine_busy(True)
        self._refine_project_id = self._project_id  # 发起时捕获，切换项目不写错
        worker = _RefineMetaWorker(
            self.store.settings,
            project,
            current_title=project.title,
            current_goal=project.goal,
            recent_context=recent_context,
            max_title_len=MAX_PROJECT_TITLE_LEN,
            parent=self,
        )
        self._refine_worker = worker
        worker.finished_ok.connect(self._on_ai_refine_ok)
        worker.failed.connect(self._on_ai_refine_failed)
        worker.start()

    def _on_ai_refine_ok(self, title: str, goal: str):
        target = self._refine_project_id or self._project_id
        self._refine_worker = None
        self._refine_project_id = None
        self._essentials.set_ai_refine_busy(False)
        if not target:
            return
        title = (title or "").strip()
        goal = (goal or "").strip()
        if not title and not goal:
            return
        patched = self.store.patch(
            target,
            **{k: v for k, v in (("title", title), ("goal", goal)) if v},
        )
        if not patched:
            return
        ev = ProjectEvent(
            kind="info",
            content=(
                f"AI 已更新项目元信息：\n"
                f"- 名称：{patched.title}\n"
                f"- 目标：{patched.goal or '（空）'}"
            ),
        )
        self.store.append_event(target, ev)
        if target == self._project_id:
            self._timeline.append_event(ev)
            self._refresh_essentials()
        self.status_changed.emit()

    def _on_ai_refine_failed(self, err: str):
        target = self._refine_project_id or self._project_id
        self._refine_worker = None
        self._refine_project_id = None
        self._essentials.set_ai_refine_busy(False)
        if target:
            ev = ProjectEvent(kind="error", content=f"AI 更新名称/目标失败：{err}")
            self.store.append_event(target, ev)
            if target == self._project_id:
                self._timeline.append_event(ev)

    def _edit_approval(self):
        if not self._project_id:
            return
        project = self.store.get(self._project_id)
        if not project:
            return
        updated = _prompt_approval_flags(self, self.theme, project.approval)
        if updated is None:
            return
        self.store.set_approval(self._project_id, updated)
        ev = ProjectEvent(
            kind="approval",
            content=f"已更新本项目审核策略：{updated.summary()}",
        )
        self.store.append_event(self._project_id, ev)
        self._timeline.append_event(ev)
        self._schedule_essentials_refresh()
        # 运行中把策略改成「全部免审」时，自动放行当前已挂起的审批，否则会一直卡在待审批。
        # 只针对审批中断（approval），不影响 ask_user 澄清提问；仅在项目处于「待审批」状态才放行，
        # 避免在普通执行中误发「已自动批准」。
        all_skipped = (
            updated.skip_read
            and updated.skip_write
            and updated.skip_routine
            and updated.skip_high_risk
        )
        running = self._worker is not None and self._worker.isRunning()
        pending_n = getattr(self._worker, "_last_pending_count", 0) or 0
        awaiting = (
            (self.store.get(self._project_id) or project).status
            == ProjectStatus.AWAITING_APPROVAL
        )
        if all_skipped and running and pending_n > 0 and awaiting:
            self._worker.approve_all()
            note = ProjectEvent(
                kind="approval",
                content=f"策略已改为全部免审，自动批准当前 {pending_n} 项待审操作。",
            )
            self.store.append_event(self._project_id, note)
            self._timeline.append_event(note)

    def _on_send(self, text: str, attachments: list | None = None):
        """非运行期：对话模式回复提问（可与目标无关），可改名称/目标。"""
        from wokbee.engine.worker import AgentWorker

        if not self._project_id:
            _tip(self, self.theme, "请先新建或选择一个项目。")
            return
        text = (text or "").strip()
        if not text and not attachments:
            return
        if self._worker and self._worker.isRunning():
            _tip(self, self.theme, "当前正在运行或对话中，可再次点击发送/运行按钮暂停。")
            return
        if self._lesson_worker and self._lesson_worker.isRunning():
            _tip(self, self.theme, "正在总结经验，请稍候。")
            return
        if self._compact_worker and self._compact_worker.isRunning():
            _tip(self, self.theme, "正在压缩上下文，请稍候。")
            return
        if self._refine_worker and self._refine_worker.isRunning():
            _tip(self, self.theme, "正在更新项目信息，请稍候。")
            return

        project = self.store.get(self._project_id)
        if not project:
            return

        attachments = attachments or []

        uev_content = text or "（发送了附件）"
        if attachments:
            if text:
                uev_content = text + "\n\n[附件] " + ", ".join(
                    (a.get("display_name") or a.get("path") and str(a.get("path")) or "附件")
                    for a in attachments
                )
            else:
                uev_content = "（发送了附件：" + ", ".join(
                    (a.get("display_name") or str(a.get("path")) or "附件") for a in attachments
                ) + "）"
        uev = ProjectEvent(kind="user", content=uev_content)
        self.store.append_event(self._project_id, uev)
        self._timeline.append_event(uev)

        self._status_before_chat = project.status
        self.store.set_status(
            self._project_id,
            ProjectStatus.RUNNING,
            current_step="对话中",
        )
        self._schedule_essentials_refresh()

        self._worker_project_id = self._project_id  # 发起时捕获，运行中切项目不串写
        self._worker_mode = "chat"
        self._worker = AgentWorker(
            self.store.settings,
            project,
            self.store.path_for(project.id),
            text,
            project.approval.copy(),
            self.store.settings.max_steps,
            parent=self,
            mode="chat",
            attachments=attachments,
        )
        self._timeline.begin_run()
        self._worker.event_emitted.connect(self._on_engine_event)
        self._worker.approval_needed.connect(self._on_approval_needed)
        self._worker.ask_user_needed.connect(self._on_ask_user_needed)
        self._worker.finished_result.connect(self._on_engine_finished)
        self._worker.model_error.connect(self._on_worker_model_error)
        self._actions.set_running(True, "chat")
        self._actions.set_cache_stats("")
        self._actions.hide_approval()
        self._worker.start()

    def _on_run(self):
        from wokbee.engine.worker import AgentWorker

        if not self._project_id:
            _tip(self, self.theme, "请先新建或选择一个项目。")
            return
        if self._worker and self._worker.isRunning():
            _tip(self, self.theme, "当前项目已在运行中。")
            return
        if self._lesson_worker and self._lesson_worker.isRunning():
            _tip(self, self.theme, "正在总结经验，请稍候。")
            return
        if self._compact_worker and self._compact_worker.isRunning():
            _tip(self, self.theme, "正在压缩上下文，请稍候。")
            return
        if self._refine_worker and self._refine_worker.isRunning():
            _tip(self, self.theme, "正在更新项目信息，请稍候。")
            return

        text, attachments = self._actions.take_input(with_attachments=True)
        project = self.store.get(self._project_id)
        if not project:
            if text:
                self._actions.set_draft(text)
            return

        goal = (project.goal or "").strip()
        if not goal:
            # 运行前必须有目标：弹窗让用户补填；取消则还原输入框
            filled = _ask_multiline(
                self,
                self.theme,
                "请填写项目目标",
                "当前项目目标为空，运行前需要先设置目标。",
                text or "",
                min_lines=5,
            )
            if not filled:
                if text:
                    self._actions.set_draft(text)
                else:
                    _tip(self, self.theme, "请先设置项目目标后再运行。")
                return
            self.store.update_goal(self._project_id, filled)
            project = self.store.get(self._project_id) or project
            goal = filled
            self._schedule_essentials_refresh()
            # 输入框内容若已用作目标，不再重复当指令；无额外指令时用目标运行
            text = ""

        project = self.store.get(self._project_id) or project

        if text:
            uev = ProjectEvent(kind="user", content=text)
            self.store.append_event(self._project_id, uev)
            self._timeline.append_event(uev)
        user_message = text or goal
        if not user_message:
            _tip(self, self.theme, "请先设置项目目标或在输入框填写指令。")
            return

        self._status_before_chat = None
        self._worker_mode = "run"
        self.store.set_status(
            self._project_id,
            ProjectStatus.RUNNING,
            current_step="Deep Agents 执行中",
            progress_done=0,
            progress_total=self.store.settings.max_steps,
        )
        self._schedule_essentials_refresh()

        self._worker_project_id = self._project_id  # 发起时捕获，运行中切项目不串写
        self._worker = AgentWorker(
            self.store.settings,
            project,
            self.store.path_for(project.id),
            user_message,
            project.approval.copy(),
            self.store.settings.max_steps,
            parent=self,
            mode="run",
            attachments=attachments,
        )
        self._timeline.begin_run()
        self._worker.event_emitted.connect(self._on_engine_event)
        self._worker.approval_needed.connect(self._on_approval_needed)
        self._worker.ask_user_needed.connect(self._on_ask_user_needed)
        self._worker.finished_result.connect(self._on_engine_finished)
        self._worker.model_error.connect(self._on_worker_model_error)
        self._actions.set_running(True, "run")
        self._actions.set_cache_stats("")
        self._actions.hide_approval()
        self._worker.start()

    def _active_project(self) -> tuple[str | None, bool]:
        """返回当前回调应写到的 (项目 id, 该项目是否正被查看)。

        运行中 worker / 压缩 / 改名回调都绑定其**发起时**的项目；若用户已切到别的项目，
        仍把事件/状态写回发起项目（保全数据），但不再污染当前可见时间线。
        """
        target = self._worker_project_id or self._project_id
        return target, target == self._project_id

    def _on_engine_event(self, kind: str, content: str, meta: object):
        target, visible = self._active_project()
        if not target:
            return
        meta_d = meta if isinstance(meta, dict) else {}
        if kind == "agent_stream":
            # 流式增量：只驱动时间线实时气泡，不落盘（完整 agent 事件到达时再定稿）
            if visible:
                self._timeline.append_stream(str(meta_d.get("target") or "text"), content)
            return
        if kind == "cache" or meta_d.get("cache"):
            now_pct = meta_d.get("now_pct")
            avg_pct = meta_d.get("avg_pct")
            if now_pct is not None or avg_pct is not None:
                now_s = f"{now_pct}%" if now_pct is not None else "—"
                avg_s = f"{avg_pct}%" if avg_pct is not None else "—"
                tag = f"cache {now_s} · avg {avg_s}"
                tip = (
                    f"本轮 hit={meta_d.get('last_hit', 0)} miss={meta_d.get('last_miss', 0)}\n"
                    f"会话 hit={meta_d.get('hit_total', 0)} miss={meta_d.get('miss_total', 0)}\n"
                    f"prefix={meta_d.get('prefix_fp') or '—'}"
                )
                self._actions.set_cache_stats(tag, tooltip=tip)
            if kind == "cache":
                # 不刷时间线，避免每轮刷屏
                return
        ev = ProjectEvent(
            kind=kind,
            content=content,
            meta=meta_d,
        )
        self.store.append_event(target, ev)
        if kind == "approval":
            # 状态写回发起项目；即便当前查看的是别的项目也要更新数据
            self.store.set_status(
                target,
                ProjectStatus.AWAITING_APPROVAL,
                current_step="等待审批",
            )
        if not visible:
            # 运行中已切到别的项目：事件仍写回发起项目，但不污染当前时间线
            return
        # 增量追加（含工具 call / callback）
        self._timeline.append_event(ev)
        # 实时状态条：工具事件由 _route_tool_event 内部驱动，这里补其余类型
        if kind == "agent":
            self._timeline._status(
                "正在思考…" if str(meta_d.get("phase") or "") == "reasoning" else "正在执行…"
            )
        elif kind == "approval":
            self._timeline._status("等待审批…", pulse=False)
        elif kind == "error":
            self._timeline._status("出现错误")
        # 名称/目标被工具改写后立刻刷新顶栏与侧栏
        if meta_d.get("project_meta") in ("title", "goal"):
            self._refresh_essentials()
            self.status_changed.emit()
        else:
            self._schedule_essentials_refresh()
        self._schedule_usage_refresh()

    def _on_approval_needed(self, pending: object):
        target, visible = self._active_project()
        items = pending if isinstance(pending, list) else []
        lines = []
        for i, act in enumerate(items, 1):
            if isinstance(act, dict):
                lines.append(
                    f"{i}. [{act.get('risk', '?')}] {act.get('name')}: {act.get('description')}"
                )
        text = "需要你审批以下工具调用：\n" + ("\n".join(lines) if lines else str(pending))
        self._actions.show_approval(text)
        if target:
            self.store.set_status(
                target,
                ProjectStatus.AWAITING_APPROVAL,
                current_step="等待审批",
            )
            if visible:
                self._schedule_essentials_refresh()
        if visible:
            self._timeline.on_approval_pending()

    def _on_ask_user_needed(self, payload: object):
        """主线程弹窗收集澄清答案，再回传后台 Agent。"""
        target, visible = self._active_project()
        data = payload if isinstance(payload, dict) else {"type": "ask_user", "questions": []}
        if target:
            self.store.set_status(
                target,
                ProjectStatus.AWAITING_APPROVAL,
                current_step="等待澄清意图",
            )
            if visible:
                self._schedule_essentials_refresh()
        dlg = AskUserDialog(data, self.theme, parent=self.window() or self)
        accepted = dlg.exec() == AskUserDialog.DialogCode.Accepted
        answers = dlg.result_payload() if accepted else {"cancelled": True}
        if self._worker and self._worker.isRunning():
            self._worker.resolve_ask_user(answers)

    def _on_approve(self):
        if self._worker and self._worker.isRunning():
            self._actions.hide_approval()
            self._timeline.resume_after_approval(approved=True)
            self._worker.approve_all()

    def _on_reject(self):
        if self._worker and self._worker.isRunning():
            self._actions.hide_approval()
            self._timeline.resume_after_approval(approved=False)
            self._worker.reject_all("用户拒绝该操作")

    def _on_worker_model_error(self, err: str):
        """worker 线程发现模型解析失败：复位 UI 并提示（不写进程事件）。"""
        target = self._worker_project_id or self._project_id
        self._worker = None
        self._worker_mode = "run"
        self._actions.set_running(False)
        self._actions.hide_approval()
        if target:
            prev = self._status_before_chat
            self._status_before_chat = None
            restore = prev if prev and prev != ProjectStatus.RUNNING else ProjectStatus.IDLE
            self.store.set_status(target, restore, current_step="待模型")
            self._schedule_essentials_refresh()
        self._worker_project_id = None
        _tip(self, self.theme, err)

    def _on_engine_finished(self, result: object):
        self._actions.set_running(False)
        self._actions.hide_approval()
        target = self._worker_project_id or self._project_id
        visible = target == self._project_id
        if visible:
            self._timeline.end_run()
        if not target:
            return
        outcome = getattr(result, "outcome", "failed")
        err = getattr(result, "error", "") or ""
        mode = self._worker_mode or "run"

        if mode in ("chat",):
            # 对话结束：尽量恢复进入前的状态，避免把「完成」冲掉
            prev = self._status_before_chat
            if outcome == "awaiting_approval":
                self.store.set_status(
                    target,
                    ProjectStatus.AWAITING_APPROVAL,
                    current_step="对话待审批",
                )
            elif outcome == "cancelled":
                restore = prev if prev and prev != ProjectStatus.RUNNING else ProjectStatus.IDLE
                self.store.set_status(
                    target,
                    restore,
                    current_step="对话已取消",
                )
            elif outcome == "failed":
                restore = prev if prev and prev != ProjectStatus.RUNNING else ProjectStatus.IDLE
                self.store.set_status(
                    target,
                    restore,
                    current_step="对话失败",
                )
                if err:
                    ev = ProjectEvent(kind="error", content=f"对话失败：{err}")
                    self.store.append_event(target, ev)
                    if visible:
                        self._timeline.append_event(ev)
            else:
                restore = prev if prev and prev != ProjectStatus.RUNNING else ProjectStatus.IDLE
                step = "空闲" if restore == ProjectStatus.IDLE else (
                    "完成" if restore == ProjectStatus.DONE else restore.value
                )
                self.store.set_status(
                    target,
                    restore,
                    current_step=step,
                )
                if mode == "chat" and outcome == "success":
                    self._append_deliverables_bubble(target)
            self._status_before_chat = None
            self._worker_mode = "run"
            self._worker = None
            self._worker_project_id = None
            self._schedule_essentials_refresh()
            self._refresh_context_usage()
            return

        # 结束类文案多由引擎事件已推送；这里只更新状态，避免重复气泡 + 全量重绘
        if outcome == "success":
            self.store.set_status(
                target,
                ProjectStatus.DONE,
                current_step="完成",
                progress_done=1,
                progress_total=1,
            )
            self._append_deliverables_bubble(target)
        elif outcome == "cancelled":
            self.store.set_status(
                target,
                ProjectStatus.IDLE,
                current_step="已取消",
            )
        elif outcome == "awaiting_approval":
            self.store.set_status(
                target,
                ProjectStatus.AWAITING_APPROVAL,
                current_step="仍待审批",
            )
        elif outcome == "incomplete":
            self.store.set_status(
                target,
                ProjectStatus.IDLE,
                current_step="未完成",
            )
        else:
            self.store.set_status(
                target,
                ProjectStatus.FAILED,
                current_step="失败",
            )
            if err:
                ev = ProjectEvent(
                    kind="error",
                    content=f"运行失败：{err}",
                )
                self.store.append_event(target, ev)
                if visible:
                    self._timeline.append_event(ev)
        project = self.store.get(target)
        if project:
            names = list_deliverable_names(
                self.store.path_for(target), limit=5
            )
            if names:
                project.artifacts_summary = ", ".join(names)
                self.store.save(project)
        self._worker = None
        self._worker_mode = "run"
        self._worker_project_id = None
        self._schedule_essentials_refresh()
        self._refresh_context_usage()

    def _on_pause(self):
        if not self._project_id:
            return
        if self._lesson_worker and self._lesson_worker.isRunning():
            _tip(self, self.theme, "正在总结经验，请稍候完成（暂不支持中途取消）。")
            return
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            mode = getattr(self._worker, "mode", self._worker_mode) or "run"
            if mode == "chat":
                msg = "用户请求暂停：正在终止当前交互（含正在执行的命令）。"
            else:
                msg = "用户请求暂停/取消当前运行。"
            ev = ProjectEvent(kind="info", content=msg)
            self.store.append_event(self._project_id, ev)
            self._timeline.append_event(ev)
            return
        self.store.set_status(self._project_id, ProjectStatus.IDLE, current_step="已暂停")
        ev = ProjectEvent(kind="info", content="当前无运行中的任务。")
        self.store.append_event(self._project_id, ev)
        self._timeline.append_event(ev)
        self._schedule_essentials_refresh()

    def _on_open_folder(self):
        if not self._project_id:
            _tip(self, self.theme, "请先选择项目。")
            return
        _open_in_explorer(self.store.path_for(self._project_id))

    def _on_open_deliverables(self, project_id: str = "", _idx: int = 0):
        target = project_id or self._project_id
        if not target:
            _tip(self, self.theme, "请先选择项目。")
            return
        path = deliverables_dir(self.store.path_for(target))
        path.mkdir(parents=True, exist_ok=True)
        _open_in_explorer(path)

    def _append_deliverables_bubble(self, target: str):
        """任务完成后追加一个「打开交付物目录」的快捷链接气泡（交互/运行模式）。"""
        if not target:
            return
        ev = ProjectEvent(
            kind="deliverables",
            content="交付物目录",
            meta={"project_id": target},
        )
        self.store.append_event(target, ev)
        if target == self._project_id and self._timeline is not None:
            self._timeline.append_event(ev)

    def _on_upload(self):
        if not self._project_id:
            _tip(self, self.theme, "请先选择项目。")
            return
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择要上传的文件",
            "",
            "所有文件 (*.*)",
        )
        if not files:
            return
        dest_dir = uploads_dir(self.store.path_for(self._project_id))
        dest_dir.mkdir(parents=True, exist_ok=True)
        import shutil
        from datetime import datetime as _dt

        saved: list[str] = []
        for src in files:
            src_path = Path(src)
            name = src_path.name
            target = dest_dir / name
            if target.exists():
                stem, suf = src_path.stem, src_path.suffix
                stamp = _dt.now().strftime("%H%M%S")
                target = dest_dir / f"{stem}_{stamp}{suf}"
            try:
                shutil.copy2(src_path, target)
                saved.append(target.name)
            except OSError as e:
                _tip(self, self.theme, f"上传失败：{name}\n{e}")
                return
        ev = ProjectEvent(
            kind="info",
            content=(
                f"已上传 {len(saved)} 个文件到 uploads/：\n"
                + "\n".join(f"- `{n}`" for n in saved)
                + "\nAgent 运行时可直接读取调用；归档时保留上传资料，不会清空。"
            ),
        )
        self.store.append_event(self._project_id, ev)
        self._timeline.append_event(ev)
        self._schedule_essentials_refresh()
        _tip(
            self,
            self.theme,
            f"已保存到 uploads/：\n" + "\n".join(saved),
            title="上传完成",
        )

    def _restore_after_lesson(self):
        target = self._worker_project_id or self._project_id
        self._lesson_worker = None
        self._worker_mode = "run"
        self._actions.set_running(False)
        if not target:
            self._status_before_lesson = None
            self._worker_project_id = None
            return
        prev = self._status_before_lesson
        self._status_before_lesson = None
        restore = prev if prev and prev != ProjectStatus.RUNNING else ProjectStatus.IDLE
        step = (
            "完成" if restore == ProjectStatus.DONE
            else (
                "失败" if restore == ProjectStatus.FAILED
                else ("空闲" if restore == ProjectStatus.IDLE else restore.value)
            )
        )
        self.store.set_status(target, restore, current_step=step)
        self._worker_project_id = None
        self._schedule_essentials_refresh()

    def _on_lesson_finished(self, lesson: object):
        target = self._worker_project_id or self._project_id
        self._restore_after_lesson()
        visible = target == self._project_id
        if not target:
            return
        # 过程事件已增量追加；成功/失败只落时间线，不再弹窗
        if not lesson:
            ev = ProjectEvent(kind="error", content="经验写入失败，请查看日志。")
            self.store.append_event(target, ev)
            if visible:
                self._timeline.append_event(ev)
        if visible:
            self._refresh_essentials()
            self._timeline._schedule_scroll_to_bottom(force=True)
        self.status_changed.emit()

    def _on_lesson_failed(self, err: str):
        target = self._worker_project_id or self._project_id
        self._restore_after_lesson()
        visible = target == self._project_id
        if target:
            ev = ProjectEvent(kind="error", content=f"总结经验失败：{err}")
            self.store.append_event(target, ev)
            if visible:
                self._timeline.append_event(ev)
                self._timeline._schedule_scroll_to_bottom(force=True)

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

    def _auto_archive_before_run(self) -> None:
        """运行前自动归档上一轮会话（无内容则跳过）。

        当前运行不再自动调用（省 token 以便重复运行复用经验）；保留此方法供外部按需触发。
        """
        if not self._project_id:
            return
        if not self.store.needs_auto_archive(self._project_id):
            return
        dest = self.store.archive_session(
            self._project_id,
            include_memory=False,
            reason="auto_before_run",
        )
        if not dest:
            return
        # 归档会清空时间线并写入一条提示，整表刷新到最新
        events, remaining = self.store.events_window(
            self._project_id, skip_from_end=0, count=INITIAL_RENDER
        )
        self._timeline.render_events(
            events, older_remaining=remaining,
            loader=self._make_events_loader(self._project_id),
        )
        self._refresh_essentials()
        self.status_changed.emit()

    def _on_archive(self):
        """归档本次会话；历史经验一并归档，仅保留最新一份经验与 scripts/。"""
        if not self._project_id:
            return
        if self._worker and self._worker.isRunning():
            return
        if self._lesson_worker and self._lesson_worker.isRunning():
            return
        ok = _confirm(
            self,
            self.theme,
            "归档",
            "确认归档本次会话？\n"
            "• 归档：对话、工作区、交付物、运行记录（uploads 上传资料保留）\n"
            "• 经验文档仅保留**最新一份**，历史经验一并归档\n"
            "• 保留：项目名称、目标、审核策略、uploads/（含参考材料）\n"
            f"• 每个项目最多保留 {MAX_ARCHIVES} 份存档，超出自动删除最旧的\n"
            "• 项目运行经验继续保留，记忆概述、跨项目记忆和对话记忆不再使用\n\n"
            "是否继续？",
        )
        if not ok:
            return
        dest = self.store.archive_session(
            self._project_id,
            include_memory=False,
            reason="archive_manual",
        )
        if not dest:
            return
        self._archive_old_experiences(self._project_id, dest)
        self._refresh()

    def _archive_old_experiences(self, project_id: str, dest: Path) -> None:
        """把 memory/experiences/ 下除最新一份外的历史经验归档到 dest，只保留最新。"""
        try:
            import shutil

            from wokbee.engine.lessons import LessonStore

            store = LessonStore(self.store.path_for(project_id))
            paths = store.list_paths()
            if len(paths) <= 1:
                return
            target_root = dest / "memory" / "experiences"
            target_root.mkdir(parents=True, exist_ok=True)
            moved = 0
            for p in paths[1:]:
                try:
                    shutil.copy2(p, target_root / p.name)
                    p.unlink(missing_ok=True)
                    moved += 1
                except OSError as e:
                    logger.warning("归档历史经验 %s 失败: %s", p, e)
            if moved:
                latest = paths[0].name
                ev = ProjectEvent(
                    kind="info",
                    content=(
                        f"已归档 {moved} 份历史经验到 `{dest.name}/memory/experiences/`；"
                        f"仅保留最新一份 `{latest}` 供复用。"
                    ),
                )
                self.store.append_event(project_id, ev)
        except Exception:
            logger.exception("归档历史经验失败（会话已归档，不影响使用）")
