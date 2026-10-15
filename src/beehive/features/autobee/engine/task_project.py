"""无人值守项目执行与事件、状态适配。"""
from __future__ import annotations
import logging
from typing import Any
from beehive.system.providers.provider_models import ResolvedModel
from beehive.features.wokbee.projects.models import ApprovalFlags, Project, ProjectEvent, ProjectStatus
from beehive.features.wokbee.projects.project_run_queue import project_run_slot
from beehive.features.autobee.core.models import ScheduledTask

logger = logging.getLogger("autobee")

def run_wokbee(executor, task: ScheduledTask) -> dict:
    # 无人值守线程内按需加载引擎（deepagents 栈），避免拖慢启动
    from beehive.features.wokbee.execution import ensure_engine_warm
    from beehive.app.execution import create_agent_runner
    from beehive.features.wokbee.execution.runner_models import RunRequest

    ensure_engine_warm()
    pid = (task.project_id or "").strip()
    if not pid:
        return {"ok": False, "message": "", "error": "未关联 WokBee 项目"}
    project = executor.project_store.get(pid)
    if project is None:
        return {"ok": False, "message": "", "error": f"WokBee 项目不存在：{pid}"}
    with project_run_slot(pid), executor._project_lock(pid):
        try:
            # 与手动运行一致：直接在当前会话继续执行，不自动归档
            resolved = executor._resolve_exec_model(task, project)
            approval = ApprovalFlags(
                skip_read=True, skip_write=True,
                skip_routine=True, skip_high_risk=True,
            )
            user_message = (task.user_message or "").strip() or project.goal or ""
            req = RunRequest(
                project=project,
                project_root=executor.project_store.path_for(project.id),
                user_message=user_message,
                resolved=resolved,
                approval=approval,
                max_steps=executor.settings.run_max_steps,
            )
            runner = create_agent_runner(executor.settings, executor.provider_store)
            executor._append_project_event(
                project.id, "user", user_message,
                {"source": "autobee", "autobee_task_id": task.id},
            )
            executor._wire_runner_callbacks(runner, task, project)
            executor.project_store.set_status(
                project.id, ProjectStatus.RUNNING, current_step="AutoBee 定时触发",
            )
            executor._emit_progress(task.id, "WokBee Agent 运行中…")
            result = runner.run(req)
        except Exception as e:
            logger.exception("定时运行 WokBee 项目失败: %s", pid)
            executor.project_store.set_status(
                project.id, ProjectStatus.FAILED, current_step="自动失败",
            )
            return {"ok": False, "message": "", "error": str(e)}

    executor._finalize_wokbee_status(project.id, result)
    outcome = getattr(result, "outcome", "failed")
    if getattr(result, "ok", False):
        # 与桌面端 WokBee 运行保持一致：AutoBee 完成后也在项目时间线
        # 追加可点击的 output/ 气泡；事件先落盘，再由 event_sink 推送到 UI。
        executor._append_project_event(
            project.id,
            "deliverables",
            "交付物目录",
            {
                "project_id": project.id,
                "source": "autobee",
                "autobee_task_id": task.id,
            },
        )
    executor._append_project_event(
        project.id, "info", f"AutoBee 任务「{task.name}」运行结束：{outcome}",
        {"source": "autobee", "autobee_task_id": task.id, "lifecycle": "finished"},
    )
    if getattr(result, "ok", False):
        message = (result.final_text or "").strip() or result.outcome
        return {"ok": True, "message": message[:4000], "error": ""}
    return {
        "ok": False,
        "message": (result.final_text or "").strip() or result.outcome,
        "error": (result.error or "")[:4000],
    }

def resolve_exec_model(executor, task: ScheduledTask, project: Project) -> ResolvedModel:
    """优先任务约束的 exec 模型，否则回落到项目自身解析链。"""
    from beehive.features.wokbee.execution.runner_assembly import resolve_model_for_project

    if task.exec_provider and task.exec_model_id:
        resolved = executor.provider_store.resolve(task.exec_provider, task.exec_model_id)
        if resolved:
            return resolved
    return resolve_model_for_project(project, executor.settings, executor.provider_store)

def wire_runner_callbacks(executor, runner: AgentRunner, task: ScheduledTask, project: Project) -> None:
    def _on_event(kind: str, content: str, meta: dict | None):
        m = dict(meta or {})
        m["autobee_task_id"] = task.id
        if kind == "agent_stream":
            executor._emit_project_event(project.id, kind, content, m)
            return
        try:
            executor._append_project_event(project.id, kind, content, m)
        except Exception:
            logger.exception("写入项目事件失败")
        brief = (content or "").replace("\n", " ").strip()
        if brief:
            executor._emit_progress(task.id, f"[{kind}] {brief[:120]}")

    def _on_approval(pending: list):
        # 无人值守：自动放行
        try:
            runner.resolve_approval([{"type": "approve"}] * len(pending))
        except Exception:
            logger.exception("自动审批失败")

    def _on_ask_user(payload: dict):
        # 无人值守：自动选择第一项，避免无限等待
        questions = payload.get("questions") or []
        answers = [
            {"id": str(q.get("id") or ""), "selected": [str(q["options"][0])]}
            for q in questions if isinstance(q, dict) and q.get("options")
        ]
        try:
            runner.resolve_ask_user({"answers": answers})
        except Exception:
            logger.exception("自动回答澄清失败")

    runner.on_event = _on_event
    runner.on_approval_needed = _on_approval
    runner.on_ask_user_needed = _on_ask_user

def append_project_event(executor, project_id: str, kind: str, content: str, meta: dict) -> None:
    event = ProjectEvent(kind=kind, content=content, meta=meta)
    executor.project_store.append_event(project_id, event)
    executor._emit_project_event(project_id, kind, content, meta)

def emit_project_event(executor, project_id: str, kind: str, content: str, meta: dict) -> None:
    if executor._event_sink is not None:
        try:
            executor._event_sink(project_id, kind, content, meta)
        except Exception:
            logger.exception("转发 AutoBee 项目实时事件失败")

def finalize_wokbee_status(executor, project_id: str, result: Any) -> None:
    outcome = getattr(result, "outcome", "failed")
    if outcome == "success":
        executor.project_store.set_status(
            project_id, ProjectStatus.DONE, current_step="完成",
            progress_done=1, progress_total=1,
        )
    elif outcome == "cancelled":
        executor.project_store.set_status(project_id, ProjectStatus.IDLE, current_step="已取消")
    else:
        executor.project_store.set_status(project_id, ProjectStatus.FAILED, current_step="自动失败")
