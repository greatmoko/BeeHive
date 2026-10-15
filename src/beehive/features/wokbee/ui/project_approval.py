"""项目审批策略与澄清；工作区显式持有项目与线程状态。"""
from __future__ import annotations
from beehive.features.wokbee.projects.models import ProjectEvent, ProjectStatus
from beehive.features.wokbee.ui.ask_user_dialog import AskUserDialog
from beehive.features.wokbee.ui.dialogs import _prompt_approval_flags
from PySide6.QtCore import QObject

class ProjectApprovalController(QObject):
    def __init__(self, workspace):
        super().__init__(workspace)
        self.workspace = workspace

    def _edit_approval(self):
        workspace = self.workspace
        if not workspace._project_id:
            return
        project = workspace.store.get(workspace._project_id)
        if not project:
            return
        updated = _prompt_approval_flags(workspace, workspace.theme, project.approval)
        if updated is None:
            return
        workspace.store.set_approval(workspace._project_id, updated)
        ev = ProjectEvent(
            kind="approval",
            content=f"已更新本项目审核策略：{updated.summary()}",
        )
        workspace.store.append_event(workspace._project_id, ev)
        workspace._timeline.append_event(ev)
        workspace._schedule_essentials_refresh()
        # 运行中把策略改成「全部免审」时，自动放行当前已挂起的审批，否则会一直卡在待审批。
        # 只针对审批中断（approval），不影响 ask_user 澄清提问；仅在项目处于「待审批」状态才放行，
        # 避免在普通执行中误发「已自动批准」。
        all_skipped = (
            updated.skip_read
            and updated.skip_write
            and updated.skip_routine
            and updated.skip_high_risk
        )
        running = workspace._worker is not None and workspace._worker.isRunning()
        pending_n = getattr(workspace._worker, "_last_pending_count", 0) or 0
        awaiting = (
            (workspace.store.get(workspace._project_id) or project).status
            == ProjectStatus.AWAITING_APPROVAL
        )
        if all_skipped and running and pending_n > 0 and awaiting:
            workspace._worker.approve_all()
            note = ProjectEvent(
                kind="approval",
                content=f"策略已改为全部免审，自动批准当前 {pending_n} 项待审操作。",
            )
            workspace.store.append_event(workspace._project_id, note)
            workspace._timeline.append_event(note)

    def _on_approval_needed(self, pending: object):
        workspace = self.workspace
        target, visible = workspace._active_project()
        items = pending if isinstance(pending, list) else []
        lines = []
        for i, act in enumerate(items, 1):
            if isinstance(act, dict):
                lines.append(
                    f"{i}. [{act.get('risk', '?')}] {act.get('name')}: {act.get('description')}"
                )
        text = "需要你审批以下工具调用：\n" + ("\n".join(lines) if lines else str(pending))
        workspace._actions.show_approval(text)
        if target:
            workspace.store.set_status(
                target,
                ProjectStatus.AWAITING_APPROVAL,
                current_step="等待审批",
            )
            if visible:
                workspace._schedule_essentials_refresh()
        if visible:
            workspace._timeline.on_approval_pending()

    def _on_ask_user_needed(self, payload: object):
        """主线程弹窗收集澄清答案，再回传后台 Agent。"""
        workspace = self.workspace
        target, visible = workspace._active_project()
        data = payload if isinstance(payload, dict) else {"type": "ask_user", "questions": []}
        if target:
            workspace.store.set_status(
                target,
                ProjectStatus.AWAITING_APPROVAL,
                current_step="等待澄清意图",
            )
            if visible:
                workspace._schedule_essentials_refresh()
        dlg = AskUserDialog(data, workspace.theme, parent=workspace.window() or workspace)
        accepted = dlg.exec() == AskUserDialog.DialogCode.Accepted
        answers = dlg.result_payload() if accepted else {"cancelled": True}
        if workspace._worker and workspace._worker.isRunning():
            workspace._worker.resolve_ask_user(answers)

    def _on_approve(self):
        workspace = self.workspace
        if workspace._worker and workspace._worker.isRunning():
            workspace._actions.hide_approval()
            workspace._timeline.resume_after_approval(approved=True)
            workspace._worker.approve_all()

    def _on_reject(self):
        workspace = self.workspace
        if workspace._worker and workspace._worker.isRunning():
            workspace._actions.hide_approval()
            workspace._timeline.resume_after_approval(approved=False)
            workspace._worker.reject_all("用户拒绝该操作")
