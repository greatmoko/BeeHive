"""项目经验生成结果；工作区显式持有项目与线程状态。"""
from __future__ import annotations
from beehive.features.wokbee.projects.models import ProjectEvent, ProjectStatus
from PySide6.QtCore import QObject

class ProjectLessonsController(QObject):
    def __init__(self, workspace):
        super().__init__(workspace)
        self.workspace = workspace

    def _restore_after_lesson(self):
        workspace = self.workspace
        target = workspace._worker_project_id or workspace._project_id
        workspace._lesson_worker = None
        workspace._worker_mode = "run"
        workspace._actions.set_running(False)
        if not target:
            workspace._status_before_lesson = None
            workspace._worker_project_id = None
            return
        prev = workspace._status_before_lesson
        workspace._status_before_lesson = None
        restore = prev if prev and prev != ProjectStatus.RUNNING else ProjectStatus.IDLE
        step = (
            "完成" if restore == ProjectStatus.DONE
            else (
                "失败" if restore == ProjectStatus.FAILED
                else ("空闲" if restore == ProjectStatus.IDLE else restore.value)
            )
        )
        workspace.store.set_status(target, restore, current_step=step)
        workspace._worker_project_id = None
        workspace._schedule_essentials_refresh()

    def _on_lesson_finished(self, lesson: object):
        workspace = self.workspace
        target = workspace._worker_project_id or workspace._project_id
        workspace.lessons._restore_after_lesson()
        visible = target == workspace._project_id
        if not target:
            return
        # 过程事件已增量追加；成功/失败只落时间线，不再弹窗
        if not lesson:
            ev = ProjectEvent(kind="error", content="经验写入失败，请查看日志。")
            workspace.store.append_event(target, ev)
            if visible:
                workspace._timeline.append_event(ev)
        if visible:
            workspace._refresh_essentials()
            workspace._timeline._schedule_scroll_to_bottom(force=True)
        workspace.status_changed.emit()

    def _on_lesson_failed(self, err: str):
        workspace = self.workspace
        target = workspace._worker_project_id or workspace._project_id
        workspace.lessons._restore_after_lesson()
        visible = target == workspace._project_id
        if target:
            ev = ProjectEvent(kind="error", content=f"总结经验失败：{err}")
            workspace.store.append_event(target, ev)
            if visible:
                workspace._timeline.append_event(ev)
                workspace._timeline._schedule_scroll_to_bottom(force=True)
