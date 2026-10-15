"""项目目标编辑与 AI 优化；工作区显式持有项目与线程状态。"""
from __future__ import annotations
from beehive.features.wokbee.projects.models import MAX_PROJECT_TITLE_LEN, ProjectEvent
from beehive.common.ui.dialogs.text_input import _ask_multiline
from beehive.common.ui.dialogs.message import show_tip as _tip
from beehive.features.wokbee.ui.project_workers import _RefineMetaWorker
from PySide6.QtCore import QObject

class ProjectMetadataController(QObject):
    def __init__(self, workspace):
        super().__init__(workspace)
        self.workspace = workspace

    def _edit_goal(self):
        workspace = self.workspace
        if not workspace._project_id:
            return
        project = workspace.store.get(workspace._project_id)
        if not project:
            return
        new_goal = _ask_multiline(
            workspace,
            workspace.theme,
            "编辑目标",
            "项目目标（多行，超出可滚动）",
            project.goal,
            min_lines=5,
        )
        if new_goal is None:
            return
        workspace.store.update_goal(workspace._project_id, new_goal)
        workspace._refresh()

    def _on_ai_refine_meta(self):
        """一键调用 AI，根据当前目标与最近时间线更新名称+目标。"""
        workspace = self.workspace
        if not workspace._project_id:
            return
        if workspace._refine_worker and workspace._refine_worker.isRunning():
            return
        if workspace._worker and workspace._worker.isRunning():
            _tip(workspace, workspace.theme, "请先等待当前运行/对话结束。")
            return
        if workspace._lesson_worker and workspace._lesson_worker.isRunning():
            _tip(workspace, workspace.theme, "正在总结经验，请稍候。")
            return
        project = workspace.store.get(workspace._project_id)
        if not project:
            return

        root = workspace.store.path_for(workspace._project_id)
        recent_events, _remaining = workspace.store.events_window(
            workspace._project_id, skip_from_end=0, count=20
        )
        context_lines = [
            f"[{ev.kind}] {(ev.content or '').strip()}"
            for ev in reversed(recent_events)
            if (ev.content or "").strip()
        ]
        recent_context = "\n".join(context_lines)[-8000:] or "（暂无最近交互记录）"
        workspace._essentials.set_ai_refine_busy(True)
        workspace._refine_project_id = workspace._project_id  # 发起时捕获，切换项目不写错
        worker = _RefineMetaWorker(
            workspace.store.settings,
            project,
            current_title=project.title,
            current_goal=project.goal,
            recent_context=recent_context,
            max_title_len=MAX_PROJECT_TITLE_LEN,
            parent=workspace,
        )
        workspace._refine_worker = worker
        worker.finished_ok.connect(workspace.metadata._on_ai_refine_ok)
        worker.failed.connect(workspace.metadata._on_ai_refine_failed)
        worker.start()

    def _on_ai_refine_ok(self, title: str, goal: str):
        workspace = self.workspace
        target = workspace._refine_project_id or workspace._project_id
        workspace._refine_worker = None
        workspace._refine_project_id = None
        workspace._essentials.set_ai_refine_busy(False)
        if not target:
            return
        title = (title or "").strip()
        goal = (goal or "").strip()
        if not title and not goal:
            return
        patched = workspace.store.patch(
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
        workspace.store.append_event(target, ev)
        if target == workspace._project_id:
            workspace._timeline.append_event(ev)
            workspace._refresh_essentials()
        workspace.status_changed.emit()

    def _on_ai_refine_failed(self, err: str):
        workspace = self.workspace
        target = workspace._refine_project_id or workspace._project_id
        workspace._refine_worker = None
        workspace._refine_project_id = None
        workspace._essentials.set_ai_refine_busy(False)
        if target:
            ev = ProjectEvent(kind="error", content=f"AI 更新名称/目标失败：{err}")
            workspace.store.append_event(target, ev)
            if target == workspace._project_id:
                workspace._timeline.append_event(ev)
