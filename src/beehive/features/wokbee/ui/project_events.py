"""项目执行事件与结束处理；工作区显式持有项目与线程状态。"""
from __future__ import annotations
from beehive.features.wokbee.projects.models import ProjectEvent, ProjectStatus
from beehive.features.wokbee.projects.paths import list_deliverable_names
from beehive.common.ui.dialogs.message import show_tip as _tip
from PySide6.QtCore import QObject

class ProjectEventsController(QObject):
    def __init__(self, workspace):
        super().__init__(workspace)
        self.workspace = workspace

    def _on_engine_event(self, kind: str, content: str, meta: object):
        workspace = self.workspace
        target, visible = workspace._active_project()
        if not target:
            return
        meta_d = meta if isinstance(meta, dict) else {}
        if kind == "context_usage":
            # 用量只更新圆环，避免每个模型或工具步骤都落成空内容的时间线卡片。
            if visible:
                try:
                    workspace._actions.set_context_usage(
                        int(meta_d.get("used", 0) or 0),
                        int(meta_d.get("limit", 0) or 0),
                        enabled=True,
                    )
                except (TypeError, ValueError):
                    pass
            return
        if kind == "agent_stream":
            # 流式增量：只驱动时间线实时气泡，不落盘（完整 agent 事件到达时再定稿）
            if visible:
                workspace._timeline.append_stream(str(meta_d.get("target") or "text"), content)
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
                workspace._actions.set_cache_stats(tag, tooltip=tip)
            if kind == "cache":
                # 不刷时间线，避免每轮刷屏
                return
        ev = ProjectEvent(
            kind=kind,
            content=content,
            meta=meta_d,
        )
        workspace.store.append_event(target, ev)
        if kind == "approval":
            # 状态写回发起项目；即便当前查看的是别的项目也要更新数据
            workspace.store.set_status(
                target,
                ProjectStatus.AWAITING_APPROVAL,
                current_step="等待审批",
            )
        if not visible:
            # 运行中已切到别的项目：事件仍写回发起项目，但不污染当前时间线
            return
        # 增量追加（含工具 call / callback）
        workspace._timeline.append_event(ev)
        # 实时状态条：工具事件由 _route_tool_event 内部驱动，这里补其余类型
        if kind == "agent":
            workspace._timeline._status(
                "正在思考…" if str(meta_d.get("phase") or "") == "reasoning" else "正在执行…"
            )
        elif kind == "approval":
            workspace._timeline._status("等待审批…", pulse=False)
        elif kind == "error":
            workspace._timeline._status("出现错误")
        # 名称/目标被工具改写后立刻刷新顶栏与侧栏
        if meta_d.get("project_meta") in ("title", "goal"):
            workspace._refresh_essentials()
            workspace.status_changed.emit()
        else:
            workspace._schedule_essentials_refresh()
        workspace.context._schedule_usage_refresh()

    def _on_worker_model_error(self, err: str):
        """worker 线程发现模型解析失败：复位 UI 并提示（不写进程事件）。"""
        workspace = self.workspace
        target = workspace._worker_project_id or workspace._project_id
        workspace._worker = None
        workspace._worker_mode = "run"
        workspace._actions.set_running(False)
        workspace._actions.hide_approval()
        if target:
            prev = workspace._status_before_chat
            workspace._status_before_chat = None
            restore = prev if prev and prev != ProjectStatus.RUNNING else ProjectStatus.IDLE
            workspace.store.set_status(target, restore, current_step="待模型")
            workspace._schedule_essentials_refresh()
        workspace._worker_project_id = None
        _tip(workspace, workspace.theme, err)

    def _on_engine_finished(self, result: object):
        workspace = self.workspace
        workspace._actions.set_running(False)
        workspace._actions.hide_approval()
        target = workspace._worker_project_id or workspace._project_id
        visible = target == workspace._project_id
        if visible:
            workspace._timeline.end_run()
        if not target:
            return
        outcome = getattr(result, "outcome", "failed")
        err = getattr(result, "error", "") or ""
        mode = workspace._worker_mode or "run"

        if mode in ("chat",):
            # 对话结束：尽量恢复进入前的状态，避免把「完成」冲掉
            prev = workspace._status_before_chat
            if outcome == "awaiting_approval":
                workspace.store.set_status(
                    target,
                    ProjectStatus.AWAITING_APPROVAL,
                    current_step="对话待审批",
                )
            elif outcome == "cancelled":
                restore = prev if prev and prev != ProjectStatus.RUNNING else ProjectStatus.IDLE
                workspace.store.set_status(
                    target,
                    restore,
                    current_step="对话已取消",
                )
            elif outcome == "failed":
                restore = prev if prev and prev != ProjectStatus.RUNNING else ProjectStatus.IDLE
                workspace.store.set_status(
                    target,
                    restore,
                    current_step="对话失败",
                )
                if err:
                    ev = ProjectEvent(kind="error", content=f"对话失败：{err}")
                    workspace.store.append_event(target, ev)
                    if visible:
                        workspace._timeline.append_event(ev)
            else:
                restore = prev if prev and prev != ProjectStatus.RUNNING else ProjectStatus.IDLE
                step = "空闲" if restore == ProjectStatus.IDLE else (
                    "完成" if restore == ProjectStatus.DONE else restore.value
                )
                workspace.store.set_status(
                    target,
                    restore,
                    current_step=step,
                )
                if mode == "chat" and outcome == "success":
                    workspace.files._append_deliverables_bubble(target)
            workspace._status_before_chat = None
            workspace._worker_mode = "run"
            workspace._worker = None
            workspace._worker_project_id = None
            workspace._schedule_essentials_refresh()
            workspace.context._refresh_context_usage()
            return

        # 结束类文案多由引擎事件已推送；这里只更新状态，避免重复气泡 + 全量重绘
        if outcome == "success":
            workspace.store.set_status(
                target,
                ProjectStatus.DONE,
                current_step="完成",
                progress_done=1,
                progress_total=1,
            )
            workspace.files._append_deliverables_bubble(target)
        elif outcome == "cancelled":
            workspace.store.set_status(
                target,
                ProjectStatus.IDLE,
                current_step="已取消",
            )
        elif outcome == "awaiting_approval":
            workspace.store.set_status(
                target,
                ProjectStatus.AWAITING_APPROVAL,
                current_step="仍待审批",
            )
        elif outcome == "incomplete":
            workspace.store.set_status(
                target,
                ProjectStatus.IDLE,
                current_step="未完成",
            )
        else:
            workspace.store.set_status(
                target,
                ProjectStatus.FAILED,
                current_step="失败",
            )
            if err:
                ev = ProjectEvent(
                    kind="error",
                    content=f"运行失败：{err}",
                )
                workspace.store.append_event(target, ev)
                if visible:
                    workspace._timeline.append_event(ev)
        project = workspace.store.get(target)
        if project:
            names = list_deliverable_names(
                workspace.store.path_for(target), limit=5
            )
            if names:
                project.artifacts_summary = ", ".join(names)
                workspace.store.save(project)
        workspace._worker = None
        workspace._worker_mode = "run"
        workspace._worker_project_id = None
        workspace._schedule_essentials_refresh()
        workspace.context._refresh_context_usage()
