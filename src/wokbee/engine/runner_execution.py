"""Deep Agents 运行器：目标 → 执行 → 审批门 → lesson。"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from deepagents import FilesystemMiddleware, create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend
from langgraph.errors import GraphInterrupt, GraphRecursionError
from langgraph.types import Command

from tokbee.core.provider_store import ProviderStore, ResolvedModel

from wokbee.core.models import Project, _now
from wokbee.core.timeline_format import (
    format_tool_call_for_timeline,
    format_tool_callback_for_timeline,
)
from wokbee.core.credential_store import redact_obj, redact_text
from wokbee.core.settings import WokBeeSettings
from wokbee.engine.approval_policy import (
    build_interrupt_on,
    risk_label_for_tool,
)
from wokbee.engine.archive_guard import ArchiveDeniedBackend, attach_execute_watch
from wokbee.engine.access_request import (
    ApprovedDirRegistry,
    build_access_request_tool,
    mount_dir,
)
from wokbee.engine.access_coerce import AccessCoerceBackend
from wokbee.engine.readonly_backend import ReadOnlyBackend
from wokbee.engine.file_tools import FILESYSTEM_TOOL_DESCRIPTIONS, build_file_tools
from wokbee.engine.lessons import (
    Lesson,
    LessonStore,
    build_lesson_digest,
    build_experience_tools,
    collect_events_log,
    collect_scripts_context,
    slice_latest_round,
    summarize_lesson_with_ai,
    sync_lesson_from_pipeline,
)
from wokbee.engine.runtime_env import build_runtime_env_block
from wokbee.engine.model_factory import build_chat_model
from wokbee.engine.network_tools import NETWORK_TOOLS
from wokbee.engine.cache_prefix import (
    CacheHitTracker,
    PrefixGuard,
    build_session_context_block,
    compose_user_with_context,
    prefix_fingerprint,
    sort_tools_by_name,
    static_system_prompt,
    tool_name_of,
    wrap_tools_truncate_results,
)
from wokbee.engine.ask_user import (
    build_ask_user_tool,
    is_ask_user_interrupt,
    normalize_ask_user_value,
)
from wokbee.engine.project_tools import build_project_meta_tools
from wokbee.engine.credential_tools import build_credential_tools
from wokbee.engine.autobee_tools import build_autobee_tools
from wokbee.engine.script_factory import (
    apply_ai_authored_scripts,
    apply_ai_pipeline_steps,
    drop_missing_pipeline_scripts,
    solidify_scripts,
)
from wokbee.engine.script_runner import (
    build_user_message_for_ai_phase,
    peek_pipeline,
    publish_pipeline_outputs,
    run_pipeline_until_ai_or_end,
)
from wokbee.core.skills_store import SkillsStore
from wokbee.core.mcp_store import McpStore
from wokbee.engine.runner_events import EventCallback, ExecutionEventLog
from wokbee.engine.runner_models import RunRequest, RunResult, StepBudget, StepLimitExceeded
from wokbee.engine.runner_sessions import (
    get_checkpointer as _get_checkpointer,
    remember_agent,
    reset_run_state as _reset_run_state,
)
from wokbee.engine.runner_assembly import (
    configure_design_write_validator,
    prepare_project_root,
    resolve_model_for_project,
)
from wokbee.engine.runner_experience import (
    fallback_notes,
    fallback_success_path,
    load_latest_round_events,
)
from wokbee.engine.runner_experience_writer import ExperienceWriterMixin
from wokbee.engine.runner_agent_assembly import AgentAssemblyMixin
from wokbee.engine.runner_flow import RunnerFlowMixin
from wokbee.engine.runner_support import (
    _attachment_content,
    _collect_messages_from_update,
    _emit_message_events,
    _extract_text,
    _first_ask_user_payload,
    _format_engine_error,
    _has_pending,
    _is_ai_message,
    _msg_fingerprint,
    _pending_from_state,
    _stream_delta_parts,
    _tool_call_id,
    _tool_calls_of,
    build_success_path_from_messages,
)

logger = logging.getLogger("wokbee")

ApprovalCallback = Callable[[list[dict]], None]  # pending action summaries
_StepBudget = StepBudget  # 私有旧名兼容

# 【会话上下文】块的固定首行，用作「是否已注入」的稳定哨兵（内容可随项目变更，
# 但首行字面量恒定）。判定已注入时以此为准，不依赖 project.title/goal 等易变内容。
_CONTEXT_SENTINEL = "【会话上下文】"



class AgentRunner(AgentAssemblyMixin, ExperienceWriterMixin, RunnerFlowMixin):
    """同步运行（应在后台线程调用）。"""

    def __init__(
        self,
        settings: WokBeeSettings | None = None,
        provider_store: ProviderStore | None = None,
    ):
        self.settings = settings or WokBeeSettings()
        self.provider_store = provider_store or ProviderStore()
        self._cancel = threading.Event()
        self._approval_event = threading.Event()
        self._approval_decisions: list[dict] | None = None
        self._ask_user_event = threading.Event()
        self._ask_user_answers: dict | None = None
        self._event_log = ExecutionEventLog()
        self._cache_tracker = CacheHitTracker()
        self._prefix_guard: PrefixGuard | None = None
        self._session_context_block: str = ""
        self._context_injected: bool = False
        # 本轮是否已由 Agent 用 update_project_experience 工具更新过经验（结束兜底据此跳过）
        self._experience_updated_by_tool: bool = False
        self._step_budget: _StepBudget | None = None
        self._seen_tool_update_ids: set[str] = set()
        self._phase_states: list[dict[str, Any]] = []
        self.on_event: EventCallback | None = None
        self.on_approval_needed: ApprovalCallback | None = None
        self.on_ask_user_needed: Callable[[dict], None] | None = None

    def request_cancel(self) -> None:
        self._cancel.set()
        try:
            from tokbee.core.subprocess_util import kill_all_cancellable_runs

            kill_all_cancellable_runs(cancel_event=self._cancel)
        except Exception:
            pass
        # 若卡在审批/澄清，解开等待
        self.resolve_approval([{"type": "reject", "message": "用户取消运行"}])
        self.resolve_ask_user({"cancelled": True})

    def _mark_experience_updated(self) -> None:
        """工具成功写入经验后标记，结束兜底据此跳过自动总结。"""
        self._experience_updated_by_tool = True

    @staticmethod
    def _graph_recursion_limit(max_steps: int) -> int:
        """Deep Agents usually consume one graph tick for model and one for tools."""
        return max(4, max(1, int(max_steps)) * 2 + 1)

    def _configure_step_budget(self, req: RunRequest) -> None:
        self._step_budget = _StepBudget(req.max_steps)
        self._seen_tool_update_ids = set()
        self._phase_states = []

    def _graph_config(self, thread_id: str, req: RunRequest) -> dict:
        # recursion_limit is the graph-level hard stop; _StepBudget is the
        # user-facing logical limit that also counts tool updates.
        return {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": self._graph_recursion_limit(req.max_steps),
        }

    def _consume_tool_updates(self, messages: list[Any]) -> None:
        if self._step_budget is None:
            return
        new_count = 0
        for msg in messages or []:
            tool_calls = _tool_calls_of(msg)
            if tool_calls:
                for index, call in enumerate(tool_calls):
                    key = _tool_call_id(call) or f"{_msg_fingerprint(msg)}:{index}"
                    if key not in self._seen_tool_update_ids:
                        self._seen_tool_update_ids.add(key)
                        new_count += 1
                continue
            cls = msg.__class__.__name__ if not isinstance(msg, dict) else str(msg.get("type") or "")
            role = getattr(msg, "type", None) or (
                msg.get("role") if isinstance(msg, dict) else ""
            ) or cls
            if "Tool" in cls or role in ("tool", "ToolMessage"):
                tool_call_id = getattr(msg, "tool_call_id", None)
                if not tool_call_id and isinstance(msg, dict):
                    tool_call_id = msg.get("tool_call_id")
                key = str(tool_call_id or _msg_fingerprint(msg))
                if key not in self._seen_tool_update_ids:
                    self._seen_tool_update_ids.add(key)
                    new_count += 1
        if new_count:
            self._step_budget.consume("tool_update", new_count)

    def _record_phase_state(
        self,
        *,
        phase_index: int,
        phase_type: str,
        status: str,
        detail: str = "",
    ) -> None:
        state = {
            "step": phase_index + 1,
            "type": phase_type,
            "status": status,
            "detail": detail[:500],
        }
        self._phase_states.append(state)
        self._emit(
            "info",
            f"管线第 {phase_index + 1} 步已结束：{status}"
            + (f"（{detail[:300]}）" if detail else ""),
            {
                "pipeline_step": phase_index,
                "pipeline_type": phase_type,
                "pipeline_status": status,
                "phase_end": True,
            },
        )

    def _cancelled_result(
        self, req: RunRequest, allow_auto_lesson: bool
    ) -> RunResult:
        return RunResult(
            ok=False,
            outcome="cancelled",
            error="已取消",
        )

    def _finalize_early_failure(
        self, req: RunRequest, result: RunResult, agent: Any, config: dict
    ) -> None:
        """Do not skip experience repair when a pipeline AI turn exits early."""
        if result.outcome not in ("failed", "incomplete"):
            return
        err = result.error or f"AI 阶段未完成（{result.outcome}）"
        self._emit("error", f"AI 阶段未完成：{err}", {"outcome": result.outcome})
        path = ""
        try:
            state = agent.get_state(config)
            values = getattr(state, "values", None) or {}
            messages = values.get("messages") if isinstance(values, dict) else None
            if messages:
                path = build_success_path_from_messages(list(messages))
        except Exception:
            pass
        lesson = self._ensure_lesson_written(
            req,
            "partial" if result.outcome == "incomplete" else "failed",
            "AI 阶段未完成",
            err,
            success_path=path,
        )
        if lesson and not result.lesson_id:
            result.lesson_id = lesson.id

    def resolve_approval(self, decisions: list[dict]) -> None:
        self._approval_decisions = decisions
        self._approval_event.set()

    def resolve_ask_user(self, answers: dict) -> None:
        self._ask_user_answers = answers if isinstance(answers, dict) else {"cancelled": True}
        self._ask_user_event.set()

    def _emit_stream_delta(self, target: str, delta: str) -> None:
        """流式增量事件（agent_stream）：只驱动 UI 实时气泡，不落盘、不入本轮轨迹。

        与 `_emit` 不同：`_emit` 会写事件缓冲（供对话/经验总结），token 级
        增量不能混进去，否则总结会读到半截文本；也不进 store，避免 events.jsonl 爆炸。
        """
        self._event_log.emit_stream(self.on_event, target, delta)

    def _emit(self, kind: str, content: str, meta: dict | None = None) -> None:
        self._event_log.emit(self.on_event, kind, content, meta)

    def _snapshot_run_events(self) -> list:
        """线程安全地取当前事件快照。"""
        return self._event_log.snapshot()

    def _wait_approval(self, pending: list[dict]) -> list[dict]:
        self._approval_event.clear()
        self._approval_decisions = None
        if self.on_approval_needed:
            self.on_approval_needed(pending)
        # 总超时兜底（真实 deadline，避免窗口关闭/审批无响应时线程永久阻塞）；默认 1 小时
        deadline = time.monotonic() + (
            float(getattr(self, "_approval_wait_timeout", 3600.0) or 3600.0)
        )
        cancelled = False
        while not self._approval_event.wait(timeout=0.5):
            if self._cancel.is_set():
                cancelled = True
                break
            if time.monotonic() >= deadline:
                logger.warning("审批等待超时，自动拒绝")
                cancelled = True
                break
        if cancelled and self._cancel.is_set():
            return [{"type": "reject", "message": "用户取消"} for _ in pending]
        decisions = self._approval_decisions or [
            {"type": "reject", "message": "无审批结果"} for _ in pending
        ]
        # 数量对齐
        if len(decisions) < len(pending):
            decisions = list(decisions) + [
                {"type": "reject", "message": "未提供决策"}
                for _ in range(len(pending) - len(decisions))
            ]
        return decisions[: len(pending) or 1]

    def _wait_ask_user(self, payload: dict) -> dict:
        self._ask_user_event.clear()
        self._ask_user_answers = None
        if self.on_ask_user_needed:
            self.on_ask_user_needed(payload)
        deadline = time.monotonic() + (
            float(getattr(self, "_ask_user_wait_timeout", 3600.0) or 3600.0)
        )
        while not self._ask_user_event.wait(timeout=0.5):
            if self._cancel.is_set():
                return {"cancelled": True}
            if time.monotonic() >= deadline:
                logger.warning("等待澄清超时，按取消处理")
                return {"cancelled": True}
        return self._ask_user_answers or {"cancelled": True}


    def _with_session_context(self, user_message: Any) -> Any:
        context = (self._session_context_block or "").strip()
        if isinstance(user_message, list):
            if not context:
                return user_message
            return [{"type": "text", "text": context}, *user_message]
        return compose_user_with_context(str(user_message or ""), context)

    def _context_already_in_state(self, agent, config: dict) -> bool:
        """读 graph 已持久化消息：历史首条 user 是否已注入【会话上下文】。

        run()/run_chat() 每次都会新建 AgentRunner，本 runner 的 _context_injected
        无法跨轮生效，因此落到 checkpointer 的持久化消息上判重：只要首条 user 的消息
        以固定哨兵头开头，就说明本会话已注入过——即使换成新 runner 也成立。
        这保证第 2+ 轮的 user 只含纯问题，命中段最小化。
        """
        try:
            state = agent.get_state(config)
        except Exception:
            return False
        values = getattr(state, "values", None) or {}
        messages = values.get("messages") if isinstance(values, dict) else None
        if not messages:
            return False
        for m in messages:
            role = str(
                getattr(m, "type", None)
                or (m.get("role") if isinstance(m, dict) else "")
                or ""
            )
            if role not in ("user", "human", "HumanMessage"):
                continue
            content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
            if _message_text(content).startswith(_CONTEXT_SENTINEL):
                return True
        return False

    def _inject_session_context_once(
        self, payload: Any, *, agent=None, config: dict | None = None
    ) -> Any:
        """仅首条用户消息注入【会话上下文】，保持后续轮次 append-only 前缀稳定。

        去重依据二选一（满足即跳过）：
        1. 本 runner 已注入过（同轮内多阶段/续跑去重）；
        2. graph 持久化消息已含固定哨兵头（跨新 runner 去重）。
        """
        if self._context_injected or not isinstance(payload, dict):
            return payload
        msgs = payload.get("messages")
        if not isinstance(msgs, list) or not msgs:
            return payload
        if agent is not None and config is not None and self._context_already_in_state(agent, config):
            self._context_injected = True
            return payload
        out_msgs = []
        injected = False
        for m in msgs:
            if (
                not injected
                and isinstance(m, dict)
                and (m.get("role") or "") == "user"
            ):
                content = m.get("content") or ""
                out_msgs.append(
                    {**m, "content": self._with_session_context(content)}
                )
                injected = True
            else:
                out_msgs.append(m)
        if injected:
            self._context_injected = True
            return {**payload, "messages": out_msgs}
        return payload
