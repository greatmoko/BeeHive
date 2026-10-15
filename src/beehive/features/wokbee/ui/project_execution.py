"""项目运行、对话与暂停；工作区显式持有项目与线程状态。"""
from __future__ import annotations
from beehive.features.wokbee.projects.models import ProjectEvent, ProjectStatus
from beehive.common.ui.dialogs.text_input import _ask_multiline
from beehive.common.ui.dialogs.message import show_tip as _tip
from PySide6.QtCore import QObject

class ProjectExecutionController(QObject):
    def __init__(self, workspace):
        super().__init__(workspace)
        self.workspace = workspace

    def _on_send(self, text: str, attachments: list | None = None):
        """非运行期：对话模式回复提问（可与目标无关），可改名称/目标。"""
        workspace = self.workspace
        from beehive.features.wokbee.execution.worker import AgentWorker

        if not workspace._project_id:
            _tip(workspace, workspace.theme, "请先新建或选择一个项目。")
            return
        text = (text or "").strip()
        if not text and not attachments:
            return
        if workspace._worker and workspace._worker.isRunning():
            _tip(workspace, workspace.theme, "当前正在运行或对话中，可再次点击发送/运行按钮暂停。")
            return
        if workspace._lesson_worker and workspace._lesson_worker.isRunning():
            _tip(workspace, workspace.theme, "正在总结经验，请稍候。")
            return
        if workspace._compact_worker and workspace._compact_worker.isRunning():
            _tip(workspace, workspace.theme, "正在压缩上下文，请稍候。")
            return
        if workspace._refine_worker and workspace._refine_worker.isRunning():
            _tip(workspace, workspace.theme, "正在更新项目信息，请稍候。")
            return

        project = workspace.store.get(workspace._project_id)
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
        workspace.store.append_event(workspace._project_id, uev)
        workspace._timeline.append_event(uev)

        workspace._status_before_chat = project.status
        workspace.store.set_status(
            workspace._project_id,
            ProjectStatus.RUNNING,
            current_step="对话中",
        )
        workspace._schedule_essentials_refresh()

        workspace._worker_project_id = workspace._project_id  # 发起时捕获，运行中切项目不串写
        workspace._worker_mode = "chat"
        workspace._worker = AgentWorker(
            workspace.store.settings,
            project,
            workspace.store.path_for(project.id),
            text,
            project.approval.copy(),
            workspace.store.settings.chat_max_steps,
            parent=workspace,
            mode="chat",
            attachments=attachments,
        )
        workspace._timeline.begin_run()
        workspace._worker.event_emitted.connect(workspace.events._on_engine_event)
        workspace._worker.approval_needed.connect(workspace.approval._on_approval_needed)
        workspace._worker.ask_user_needed.connect(workspace.approval._on_ask_user_needed)
        workspace._worker.finished_result.connect(workspace.events._on_engine_finished)
        workspace._worker.model_error.connect(workspace.events._on_worker_model_error)
        workspace._actions.set_running(True, "chat")
        workspace._actions.set_cache_stats("")
        workspace._actions.hide_approval()
        workspace._worker.start()

    def _on_run(self):
        workspace = self.workspace
        from beehive.features.wokbee.execution.worker import AgentWorker

        if not workspace._project_id:
            _tip(workspace, workspace.theme, "请先新建或选择一个项目。")
            return
        if workspace._worker and workspace._worker.isRunning():
            _tip(workspace, workspace.theme, "当前项目已在运行中。")
            return
        if workspace._lesson_worker and workspace._lesson_worker.isRunning():
            _tip(workspace, workspace.theme, "正在总结经验，请稍候。")
            return
        if workspace._compact_worker and workspace._compact_worker.isRunning():
            _tip(workspace, workspace.theme, "正在压缩上下文，请稍候。")
            return
        if workspace._refine_worker and workspace._refine_worker.isRunning():
            _tip(workspace, workspace.theme, "正在更新项目信息，请稍候。")
            return

        project = workspace.store.get(workspace._project_id)
        if not project:
            return

        goal = (project.goal or "").strip()
        if not goal:
            # 运行只校验项目目标；不取走输入框草稿或附件。
            filled = (_ask_multiline(
                workspace,
                workspace.theme,
                "请填写项目目标",
                "当前项目目标为空，运行前需要先设置目标。",
                "",
                min_lines=5,
            ) or "").strip()
            if not filled:
                _tip(workspace, workspace.theme, "请先设置项目目标后再运行。")
                return
            workspace.store.update_goal(workspace._project_id, filled)
            project = workspace.store.get(workspace._project_id) or project
            goal = filled
            workspace._schedule_essentials_refresh()

        project = workspace.store.get(workspace._project_id) or project
        user_message = goal

        workspace._status_before_chat = None
        workspace._worker_mode = "run"
        workspace.store.set_status(
            workspace._project_id,
            ProjectStatus.RUNNING,
            current_step="Deep Agents 执行中",
            progress_done=0,
            progress_total=workspace.store.settings.run_max_steps,
        )
        workspace._schedule_essentials_refresh()

        workspace._worker_project_id = workspace._project_id  # 发起时捕获，运行中切项目不串写
        workspace._worker = AgentWorker(
            workspace.store.settings,
            project,
            workspace.store.path_for(project.id),
            user_message,
            project.approval.copy(),
            workspace.store.settings.run_max_steps,
            parent=workspace,
            mode="run",
            attachments=[],
        )
        workspace._timeline.begin_run()
        workspace._worker.event_emitted.connect(workspace.events._on_engine_event)
        workspace._worker.approval_needed.connect(workspace.approval._on_approval_needed)
        workspace._worker.ask_user_needed.connect(workspace.approval._on_ask_user_needed)
        workspace._worker.finished_result.connect(workspace.events._on_engine_finished)
        workspace._worker.model_error.connect(workspace.events._on_worker_model_error)
        workspace._actions.set_running(True, "run")
        workspace._actions.set_cache_stats("")
        workspace._actions.hide_approval()
        workspace._worker.start()

    def _on_pause(self):
        workspace = self.workspace
        if not workspace._project_id:
            return
        if workspace._lesson_worker and workspace._lesson_worker.isRunning():
            _tip(workspace, workspace.theme, "正在总结经验，请稍候完成（暂不支持中途取消）。")
            return
        if workspace._worker and workspace._worker.isRunning():
            workspace._worker.cancel()
            mode = getattr(workspace._worker, "mode", workspace._worker_mode) or "run"
            if mode == "chat":
                msg = "用户请求暂停：正在终止当前交互（含正在执行的命令）。"
            else:
                msg = "用户请求暂停/取消当前运行。"
            ev = ProjectEvent(kind="info", content=msg)
            workspace.store.append_event(workspace._project_id, ev)
            workspace._timeline.append_event(ev)
            return
        workspace.store.set_status(workspace._project_id, ProjectStatus.IDLE, current_step="已暂停")
        ev = ProjectEvent(kind="info", content="当前无运行中的任务。")
        workspace.store.append_event(workspace._project_id, ev)
        workspace._timeline.append_event(ev)
        workspace._schedule_essentials_refresh()
