"""业务运行器装配与稳定调用入口，通用执行状态归 ExecutionRuntime。"""
from __future__ import annotations
import logging
from collections.abc import Callable
from typing import Any
from beehive.system.providers.provider_store import ProviderStore
from beehive.features.wokbee.settings import WokBeeSettings
from beehive.system.agent.cache_stats import CacheHitTracker
from beehive.system.agent.prefix_guard import PrefixGuard
from beehive.system.agent.execution_events import EventCallback
from beehive.features.wokbee.execution.runner_models import RunRequest, RunResult, StepBudget
from beehive.features.wokbee.execution.runner_experience_writer import ExperienceWriterMixin
from beehive.features.wokbee.execution.runner_agent_assembly import AgentAssemblyMixin
from beehive.features.wokbee.execution.runner_flow import RunnerFlowMixin
from beehive.system.agent.message_content import _msg_fingerprint
from beehive.system.agent.message_content import _tool_call_id
from beehive.system.agent.message_content import _tool_calls_of
from beehive.features.wokbee.execution.runner_support import build_success_path_from_messages
from beehive.system.agent.execution_runtime import ExecutionRuntime
from beehive.features.wokbee.execution import runner_context as context

logger = logging.getLogger("wokbee")
ApprovalCallback = Callable[[list[dict]], None]
_StepBudget = StepBudget
_CONTEXT_SENTINEL = "【会话上下文】"

class AgentRunner(AgentAssemblyMixin, ExperienceWriterMixin, RunnerFlowMixin):
    def __init__(
        self,
        settings: WokBeeSettings | None = None,
        provider_store: ProviderStore | None = None,
        *, task_tools_factory=None, design_write_validator=None,
        design_question_preparer=None, design_prepare_root=None,
    ):
        self.settings = settings or WokBeeSettings()
        self.provider_store = provider_store or ProviderStore()
        self.runtime = ExecutionRuntime()
        self.task_tools_factory = task_tools_factory
        self.design_write_validator = design_write_validator
        self.design_question_preparer = design_question_preparer
        self.design_prepare_root = design_prepare_root
        self._cache_tracker = CacheHitTracker()
        self._prefix_guard: PrefixGuard | None = None
        self._session_context_block: str = ""
        self._context_injected: bool = False
        # 本轮是否已由 Agent 用 update_project_experience 工具更新过经验（结束兜底据此跳过）
        self._experience_updated_by_tool: bool = False
        self._step_budget: _StepBudget | None = None
        self._seen_tool_update_ids: set[str] = set()
        self.on_event: EventCallback | None = None
        self.on_approval_needed: ApprovalCallback | None = None
        self.on_ask_user_needed: Callable[[dict], None] | None = None

    def _mark_experience_updated(self) -> None:
        """工具成功写入经验后标记，结束兜底据此跳过自动总结。"""
        self._experience_updated_by_tool = True

    @staticmethod
    def _graph_recursion_limit(max_steps: int) -> int:
        """Deep Agents 通常分别为模型调用和工具调用消耗图步数。"""
        return max(4, max(1, int(max_steps)) * 2 + 1)

    def _configure_step_budget(self, req: RunRequest) -> None:
        self._step_budget = _StepBudget(req.max_steps)
        self._seen_tool_update_ids = set()

    def _graph_config(self, thread_id: str, req: RunRequest) -> dict:
        # 图递归上限作为硬停止边界，逻辑预算另计工具更新。
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
        """AI 轮次提前结束时仍执行经验修复。"""
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

    def _with_session_context(self, user_message: Any):
        return context._with_session_context(self, user_message)

    def _context_already_in_state(self, agent, config: dict):
        return context._context_already_in_state(self, agent, config)

    def _inject_session_context_once(self, payload: Any, *, agent=None, config: dict | None=None):
        return context._inject_session_context_once(self, payload, agent=agent, config=config)

    def request_cancel(self):
        return self.runtime.request_cancel()

    def resolve_approval(self, decisions: list[dict]):
        return self.runtime.resolve_approval(decisions)

    def resolve_ask_user(self, answers: dict):
        return self.runtime.resolve_ask_user(answers)

    def _emit_stream_delta(self, target: str, delta: str):
        return self.runtime._emit_stream_delta(target, delta)

    def _emit(self, kind: str, content: str, meta: dict | None=None):
        return self.runtime._emit(kind, content, meta)

    def _snapshot_run_events(self):
        return self.runtime._snapshot_run_events()

    def _wait_approval(self, pending: list[dict]):
        try:
            timeout = self.settings.get("approval_timeout_seconds", 43200)
        except (TypeError, ValueError, OverflowError):
            timeout = 43200
        return self.runtime._wait_approval(pending, timeout=timeout)

    def _wait_ask_user(self, payload: dict):
        return self.runtime._wait_ask_user(payload)

    @property
    def _cancel(self):
        return self.runtime._cancel

    @_cancel.setter
    def _cancel(self, value):
        self.runtime._cancel = value


    @property
    def _event_log(self):
        return self.runtime._event_log

    @_event_log.setter
    def _event_log(self, value):
        self.runtime._event_log = value

    @property
    def on_event(self):
        return self.runtime.on_event

    @on_event.setter
    def on_event(self, value):
        self.runtime.on_event = value

    @property
    def on_approval_needed(self):
        return self.runtime.on_approval_needed

    @on_approval_needed.setter
    def on_approval_needed(self, value):
        self.runtime.on_approval_needed = value

    @property
    def on_ask_user_needed(self):
        return self.runtime.on_ask_user_needed

    @on_ask_user_needed.setter
    def on_ask_user_needed(self, value):
        self.runtime.on_ask_user_needed = value
