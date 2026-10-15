"""项目上下文与模型；工作区显式持有项目与线程状态。"""
from __future__ import annotations
from beehive.system.memory import context as ctxman
from beehive.system.providers.ai_client import AIClient
from beehive.system.providers.provider_store import ProviderStore
from beehive.features.wokbee.projects.context_usage import estimate_project_usage, load_context_state, plan_project_compaction, save_context_state
from beehive.features.wokbee.projects.models import ProjectEvent
from beehive.common.ui.dialogs.message import show_tip as _tip
from beehive.features.wokbee.ui.project_workers import _CompactWorker
from PySide6.QtCore import QObject

class ProjectContextController(QObject):
    def __init__(self, workspace):
        super().__init__(workspace)
        self.workspace = workspace

    def _on_model_changed(self, provider_id: str, model_id: str):
        workspace = self.workspace
        if not workspace._project_id:
            # 无项目时：同步到厂商「默认模型」，避免写进 WokBee 旧配置覆盖厂商默认
            if provider_id and model_id:
                try:
                    ProviderStore().set_default_model(provider_id, model_id)
                except Exception:
                    pass
            return
        project = workspace.store.get(workspace._project_id)
        if not project:
            return
        if project.provider == provider_id and project.model_id == model_id:
            workspace.context._refresh_context_usage()
            return
        project.provider = provider_id or ""
        project.model_id = model_id or ""
        workspace.store.save(project)
        # 气泡用厂商显示名，与模型选择按钮一致（勿用 provider_id）
        display = (workspace._actions._model_combo.current_label() or "").strip()
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
        workspace.store.append_event(workspace._project_id, ev)
        workspace._timeline.append_event(ev)
        workspace.context._refresh_context_usage()

    def _schedule_usage_refresh(self):
        workspace = self.workspace
        workspace._usage_timer.start()

    def _context_window_for_current(self) -> int:
        workspace = self.workspace
        provider_id, model_id = workspace._actions.selected_model()
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
        workspace = self.workspace
        if not workspace._project_id:
            workspace._actions.set_context_usage(0, 0, enabled=False)
            return
        root = workspace.store.path_for(workspace._project_id)
        events, _remaining = workspace.store.events_window(
            workspace._project_id, skip_from_end=0, count=500
        )
        usage = estimate_project_usage(
            events=events,
            project_root=root,
            context_window=workspace.context._context_window_for_current(),
            draft_text=workspace._actions.draft_text(),
        )
        busy = bool(workspace._compact_worker and workspace._compact_worker.isRunning())
        workspace._actions.set_context_usage(
            usage.used, usage.limit, enabled=not busy,
        )

    def _on_compress_clicked(self):
        workspace = self.workspace
        if not workspace._project_id:
            _tip(workspace, workspace.theme, "请先新建或选择一个项目。")
            return
        if workspace._compact_worker and workspace._compact_worker.isRunning():
            return
        if workspace._worker and workspace._worker.isRunning():
            _tip(workspace, workspace.theme, "Agent 运行中，请稍后再压缩。")
            return
        root = workspace.store.path_for(workspace._project_id)
        events = workspace.store.list_events(workspace._project_id)
        plan = plan_project_compaction(events, root)
        if plan is None:
            _tip(workspace, workspace.theme, "当前上下文较短，无需压缩。")
            return

        to_compact, _retained, new_boundary, prev_summary, pin_end = plan
        client = None
        resolved = None
        provider_id, model_id = workspace._actions.selected_model()
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

        workspace._compact_project_id = workspace._project_id  # 发起时捕获，切换项目不写错
        worker = _CompactWorker(
            client,
            to_compact,
            prev_summary,
            new_boundary,
            parent=workspace,
            pin_end=pin_end,
            model=resolved,
        )
        workspace._compact_worker = worker
        worker.compact_done.connect(workspace.context._on_compact_done)
        worker.failed.connect(workspace.context._on_compact_failed)
        worker.start()
        workspace.context._refresh_context_usage()

    def _on_compact_done(self, summary: str, boundary: int, pin_end: int = 0):
        workspace = self.workspace
        target = workspace._compact_project_id or workspace._project_id
        workspace._compact_worker = None
        workspace._compact_project_id = None
        if not target:
            return
        root = workspace.store.path_for(target)
        state = load_context_state(root)
        state["compaction_points"] = ctxman.append_compaction_point(
            state.get("compaction_points") or [],
            summary=summary,
            boundary_index=boundary,
            pin_end=pin_end,
        )
        save_context_state(root, state)
        workspace.context._refresh_context_usage()
        _tip(workspace, workspace.theme, "已压缩上下文（已钉住首条任务前缀）。")

    def _on_compact_failed(self, err: str):
        workspace = self.workspace
        workspace._compact_worker = None
        workspace._compact_project_id = None
        workspace.context._refresh_context_usage()
        _tip(workspace, workspace.theme, f"压缩失败：{err}")
