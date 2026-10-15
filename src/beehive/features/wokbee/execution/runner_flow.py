"""Agent 流式执行、审批中断、聊天与经验管线编排。"""

from __future__ import annotations

import logging
from typing import Any


from beehive.features.wokbee.execution.runner_models import RunRequest, RunResult
from beehive.features.wokbee.execution.runner_support import _emit_message_events
from beehive.features.wokbee.execution.interrupt_state import _has_pending
from beehive.features.wokbee.execution.interrupt_state import _pending_from_state

from beehive.features.wokbee.execution.runner_modes import mode_policy
from beehive.features.wokbee.execution.memory_runtime import memory_turn
from beehive.features.wokbee.execution import runner_conversation as conversation
from beehive.features.wokbee.execution.runner_pipeline import run_pipeline
from beehive.system.agent.stream_execution import stream_until_pause
from beehive.system.agent.interrupt_execution import resume_interrupts


logger = logging.getLogger("wokbee")


class RunnerFlowMixin:
    def _stream_until_pause(self, agent, input_payload, config: dict, seen: set[str]) -> None:
        """流式执行，边跑边把消息推到时间线；遇 interrupt 正常返回。

        stream 本身会堵在工具调用里。放到旁路线程跑，本线程轮询暂停：点暂停后立刻
        杀 execute 进程树；若 stream 仍不退出则放弃等待，避免 UI 永远「运行中」。
        """
        if self._cancel.is_set():
            return
        if isinstance(input_payload, dict):
            for message in input_payload.get("messages", []):
                if isinstance(message, dict) and message.get("role") == "user":
                    label = getattr(self, "_mode_label", "")
                    content = message.get("content")
                    if label:
                        marker = f"【当前模式】{label}"
                        if isinstance(content, str) and not (content.startswith(marker) or content.startswith("【会话上下文】") and marker in content):
                            message["content"] = f"{marker}\n\n{content}"
                        elif isinstance(content, list):
                            first = content[0] if content else {}
                            text = first.get("text", "") if isinstance(first, dict) else ""
                            if not (text.startswith(marker) or text.startswith("【会话上下文】") and marker in text):
                                message["content"] = [{"type": "text", "text": marker}, *content]
                    if getattr(self, "_memory_turn_id", None):
                        message.setdefault("additional_kwargs", {})["memory_turn_id"] = self._memory_turn_id
                        message["additional_kwargs"]["memory_global"] = getattr(self, "_memory_global", "")
        return stream_until_pause(self.runtime, agent, input_payload, config, seen,
                                  step_budget=self._step_budget,
                                  consume_updates=self._consume_tool_updates,
                                  emit_message=lambda message, ids: _emit_message_events(
                                  self._emit, message, ids, cache_tracker=self._cache_tracker),
                              check_prefix=self._check_prefix_guard,
                              emit_stream_delta=self._emit_stream_delta, emit=self._emit)

    def _check_prefix_guard(self, agent, config: dict) -> None:
        """轮次结束后校验消息历史 append-only；发现改写则归因到具体消息。"""
        if self._prefix_guard is None:
            return
        try:
            state = agent.get_state(config)
            values = getattr(state, "values", None) or {}
            messages = values.get("messages") if isinstance(values, dict) else None
            if not messages:
                return
            self._prefix_guard.check(messages)
        except Exception:
            logger.exception("前缀护栏检查失败")

    def _emit_script_items(self, items: list) -> None:
        for item in items:
            if item.ok:
                preview = (item.output or "")[:600]
                self._emit(
                    "tool",
                    f"callback: 本地脚本 `{item.path}` 成功：\n{preview}",
                    {"script": item.path, "step_id": item.step_id},
                )
            else:
                self._emit(
                    "error",
                    f"本地脚本 `{item.path}` 失败：{item.error or (item.output or '')[:400]}",
                    {"script": item.path, "step_id": item.step_id},
                )

    def _drain_pending_interrupts(self, agent, config, seen_msg_ids, req, *, allow_auto_lesson):
        outcome = resume_interrupts(self.runtime, agent, config, seen_msg_ids,
                                    stream=self._stream_until_pause,
                                    parse_pending=_pending_from_state,
                                    wait_approval=self._wait_approval,
                                    wait_ask_user=self._wait_ask_user, emit=self._emit)
        return RunResult(**outcome) if outcome is not None else None

    def _run_agent_turn(
        self,
        agent,
        config: dict,
        seen_msg_ids: set[str],
        req: RunRequest,
        *,
        payload: Any,
        first: bool,
        allow_auto_lesson: bool = True,
        start_hint: str = "",
    ) -> RunResult | None:
        """跑一轮流式 + 审批。返回 None 表示可继续；返回 RunResult 表示应立刻结束。"""
        if first:
            self._emit(
                "agent",
                start_hint or "开始本阶段 AI 执行（过程将实时显示）…",
                {"phase": "hint"},
            )
            self._stream_until_pause(
                agent,
                self._inject_session_context_once(payload, agent=agent, config=config),
                config,
                seen_msg_ids,
            )
        else:
            self._emit("agent", "继续下一阶段 AI 执行…", {"phase": "hint"})
            self._stream_until_pause(
                agent,
                self._inject_session_context_once(payload, agent=agent, config=config),
                config,
                seen_msg_ids,
            )

        # 暂停后立刻结束本轮，不再进入审批等待
        if self._cancel.is_set():
            return self._cancelled_result(req, allow_auto_lesson)

        early = self._drain_pending_interrupts(
            agent,
            config,
            seen_msg_ids,
            req,
            allow_auto_lesson=allow_auto_lesson,
        )
        if early is not None:
            return early

        if self._cancel.is_set():
            return self._cancelled_result(req, allow_auto_lesson)

        if _has_pending(agent, config):
            pending = _pending_from_state(agent, config)
            return RunResult(
                ok=False,
                outcome="awaiting_approval",
                pending_actions=pending,
            )
        return None

    @memory_turn
    def run_chat(self, req: RunRequest) -> RunResult:
        """交互入口；兼容旧调用方通过 runner_mode 选择设计模式。"""
        self._begin_event_round(req, round_type="chat")
        policy = mode_policy(getattr(req, "runner_mode", "") or "chat")
        if policy.pipeline:
            return self.run(req)
        return self._run_conversation(req, policy)

    @memory_turn
    def run_design(self, req: RunRequest) -> RunResult:
        """设计入口：保留对话历史，不读取项目时间线或运行经验管线。"""
        self._begin_event_round(req, round_type="design")
        return self._run_conversation(req, mode_policy("design"))

    def _run_conversation(self, req, policy):
        return conversation._run_conversation(self, req, policy)

    @staticmethod
    def _recent_events_digest(project_root, *, limit=40):
        return conversation._recent_events_digest(project_root, limit=limit)

    def _begin_event_round(self, req, *, round_type: str) -> None:
        """把本轮登记进 memory/events.jsonl 轮次上下文（schema 的 round 字段）。

        运行模式只记项目目标；交互模式（chat/design）只记用户对话。
        round_id 由 begin_round 自动生成短 ID（r+轮号）。
        """
        try:
            from beehive.features.wokbee.projects.paths import events_path
            from beehive.features.wokbee.projects.round_events import begin_round
            user_input = (getattr(req, "user_message", "") or "") if round_type != "run" else ""
            goal = (getattr(req.project, "goal", "") or "") if round_type == "run" else ""
            ctx = begin_round(
                req.project.id,
                round_type=round_type,
                user_input=user_input,
                goal=goal,
                events_file=events_path(req.project_root),
            )
            if ctx and ctx.get("round_id"):
                self._memory_turn_id = ctx["round_id"]
        except Exception:
            logger.exception("登记事件轮次失败（不影响本轮执行）")

    @memory_turn
    def run(self, req, *, resume=False):
        if resume:
            # 审批等待及其恢复属于同一轮：续用 events.jsonl 里未结束的轮次，
            # 并把本轮 turn_id 对齐回原轮次 ID，中间件分组才能合并。
            try:
                from beehive.features.wokbee.projects.paths import events_path
                from beehive.features.wokbee.projects.round_events import current_round, begin_round
                events_file = events_path(req.project_root)
                ctx = current_round(req.project.id, events_file)
                if ctx and ctx.get("round_id"):
                    begin_round(
                        req.project.id,
                        round_id=ctx["round_id"],
                        round_type=ctx.get("round_type") or "run",
                        user_input=ctx.get("user_input") or "",
                        goal=ctx.get("goal") or "",
                        events_file=events_file,
                        reuse=True,
                    )
                    self._memory_turn_id = ctx["round_id"]
            except Exception:
                logger.exception("恢复事件轮次失败（不影响本轮执行）")
        else:
            self._begin_event_round(req, round_type="run")
        return run_pipeline(self, req, resume=resume)
