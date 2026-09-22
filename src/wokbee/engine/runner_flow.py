"""Agent 流式执行、审批中断、聊天与经验管线编排。"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from langgraph.errors import GraphInterrupt, GraphRecursionError
from langgraph.types import Command

from wokbee.core.models import _now
from wokbee.engine.lessons import slice_latest_round
from wokbee.engine.runner_models import RunRequest, RunResult, StepLimitExceeded
from wokbee.engine.runner_sessions import reset_run_state as _reset_run_state
from wokbee.engine.script_runner import (
    build_user_message_for_ai_phase,
    publish_pipeline_outputs,
    run_pipeline_until_ai_or_end,
)
from wokbee.engine.runner_support import (
    _attachment_content,
    _collect_messages_from_update,
    _emit_message_events,
    _extract_text,
    _first_ask_user_payload,
    _format_engine_error,
    _has_pending,
    _is_ai_message,
    _pending_from_state,
    _stream_delta_parts,
    build_success_path_from_messages,
)

from wokbee.engine.runner_modes import ModePolicy, mode_policy
from wokbee.engine.memory_runtime import memory_turn


logger = logging.getLogger("wokbee")


class RunnerFlowMixin:
    def _stream_until_pause(self, agent, input_payload, config: dict, seen: set[str]) -> None:
        """流式执行，边跑边把消息推到时间线；遇 interrupt 正常返回。

        stream 本身会堵在工具调用里。放到旁路线程跑，本线程轮询暂停：点暂停后立刻
        杀 execute 进程树；若 stream 仍不退出则放弃等待，避免 UI 永远「运行中」。
        """
        if self._cancel.is_set():
            return
        if isinstance(input_payload, dict) and getattr(self, "_memory_turn_id", None):
            for message in input_payload.get("messages", []):
                if isinstance(message, dict) and message.get("role") == "user":
                    message.setdefault("additional_kwargs", {})["memory_turn_id"] = self._memory_turn_id
                    message["additional_kwargs"]["memory_global"] = getattr(self, "_memory_global", "")
        if self._step_budget is not None:
            self._step_budget.consume("agent_turn")

        from tokbee.core.subprocess_util import kill_all_cancellable_runs

        stream_error: list[BaseException] = []

        def _run_stream() -> None:
            pending: dict[str, list[str]] = {"reasoning": [], "text": []}
            last_flush = 0.0
            FLUSH_INTERVAL = 0.08

            def _flush(force: bool = False) -> None:
                nonlocal last_flush
                now = time.monotonic()
                if not force and now - last_flush < FLUSH_INTERVAL:
                    return
                for _target in ("reasoning", "text"):
                    parts = pending[_target]
                    if not parts:
                        continue
                    delta = "".join(parts)
                    parts.clear()
                    if delta:
                        self._emit_stream_delta(_target, delta)
                last_flush = now

            try:
                for chunk in agent.stream(
                    input_payload,
                    config=config,
                    stream_mode=["messages", "updates"],
                ):
                    if self._cancel.is_set():
                        break
                    mode, payload = chunk
                    if mode == "messages":
                        msg_chunk, _meta = payload
                        if _is_ai_message(msg_chunk):
                            r_delta, t_delta = _stream_delta_parts(msg_chunk)
                            if r_delta:
                                pending["reasoning"].append(r_delta)
                            if t_delta:
                                pending["text"].append(t_delta)
                            _flush()
                    else:
                        _flush(force=True)
                        messages = _collect_messages_from_update(payload)
                        self._consume_tool_updates(messages)
                        for msg in messages:
                            _emit_message_events(
                                self._emit,
                                msg,
                                seen,
                                cache_tracker=self._cache_tracker,
                            )
                _flush(force=True)
            except GraphInterrupt:
                pass
            except StepLimitExceeded as e:
                stream_error.append(e)
            except GraphRecursionError:
                if self._step_budget is not None:
                    stream_error.append(
                        StepLimitExceeded(
                            self._step_budget.limit,
                            self._step_budget.used,
                            "graph_recursion",
                        )
                    )
                else:
                    stream_error.append(
                        RuntimeError("Deep Agents graph recursion limit reached")
                    )
            except Exception as e:  # noqa: BLE001
                stream_error.append(e)

        t = threading.Thread(target=_run_stream, name="wokbee-agent-stream", daemon=True)
        t.start()
        while t.is_alive():
            t.join(0.25)
            if not self._cancel.is_set():
                continue
            kill_all_cancellable_runs(cancel_event=self._cancel)
            t.join(8)
            if t.is_alive():
                logger.warning("暂停后 stream 未退出，放弃等待并结束本轮")
                self._emit("info", "已暂停：正在退出执行管线（已终止本机命令）。")
            break
        if stream_error and not self._cancel.is_set():
            raise stream_error[0]
        self._check_prefix_guard(agent, config)

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

    def _drain_pending_interrupts(
        self,
        agent,
        config: dict,
        seen_msg_ids: set[str],
        req: RunRequest,
        *,
        allow_auto_lesson: bool,
    ) -> RunResult | None:
        """处理 ask_user / 工具审批中断，直到无 pending 或需外部等待。"""
        guard = 0
        while _has_pending(agent, config) and guard < 50:
            guard += 1
            if self._cancel.is_set():
                return RunResult(
                    ok=False,
                    outcome="cancelled",
                    error="已取消",
                )

            ask_payload = _first_ask_user_payload(agent, config)
            if ask_payload:
                n = len(ask_payload.get("questions") or [])
                self._emit(
                    "info",
                    f"AI 需要你澄清意图（{n} 题），请在弹窗中作答…",
                    {"ask_user": ask_payload},
                )
                answers = self._wait_ask_user(ask_payload)
                if self._cancel.is_set():
                    return RunResult(
                        ok=False,
                        outcome="cancelled",
                        error="已取消",
                    )
                if answers.get("cancelled"):
                    self._emit("info", "你取消了澄清提问。")
                else:
                    self._emit("info", "已收到你的澄清回答，继续执行…")
                self._stream_until_pause(
                    agent,
                    Command(resume=answers),
                    config,
                    seen_msg_ids,
                )
                continue

            pending = _pending_from_state(agent, config)
            if not pending:
                break

            lines = []
            for i, act in enumerate(pending, 1):
                lines.append(
                    f"{i}. [{act.get('risk')}] {act.get('name')}: {act.get('description')}"
                )
            self._emit(
                "approval",
                "需要审批以下操作：\n" + "\n".join(lines),
                {"pending": pending},
            )

            decisions = self._wait_approval(pending)
            if self._cancel.is_set():
                return RunResult(
                    ok=False,
                    outcome="cancelled",
                    error="已取消",
                )

            approved = sum(1 for d in decisions if d.get("type") == "approve")
            rejected = len(decisions) - approved
            self._emit(
                "approval",
                f"审批结果：通过 {approved}，拒绝 {rejected}",
                {"decisions": decisions},
            )

            if self._approval_timed_out:
                return RunResult(ok=False, outcome="failed", error="审批等待超时，已拒绝并结束本轮")

            self._stream_until_pause(
                agent,
                Command(resume={"decisions": decisions}),
                config,
                seen_msg_ids,
            )
        return None

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
        policy = mode_policy(getattr(req, "runner_mode", "") or "chat")
        if policy.pipeline:
            return self.run(req)
        return self._run_conversation(req, policy)

    @memory_turn
    def run_design(self, req: RunRequest) -> RunResult:
        """设计入口：保留对话历史，不读取项目时间线或运行经验管线。"""
        return self._run_conversation(req, mode_policy("design"))

    def _run_conversation(self, req: RunRequest, policy: ModePolicy) -> RunResult:
        """对话共用流程；模式差异由策略提供。"""
        self._event_log.reset()
        self._context_injected = False
        # 保留设计模式已有的 thread_id，升级后继续使用原 checkpoint。
        thread_id = f"wokbee-chat-{req.project.id}"
        chat_thread_id = str(getattr(req, "chat_thread_id", "") or "").strip()
        if chat_thread_id:
            thread_id += f"-{chat_thread_id}"
        self._configure_step_budget(req)
        config = self._graph_config(thread_id, req)
        seen_msg_ids: set[str] = set()

        question = (req.user_message or "").strip()
        if not question and not req.attachments:
            return RunResult(ok=False, outcome="failed", error="提问内容为空")

        try:
            agent = self.build_agent(req, mode=policy.name)
        except Exception as e:
            logger.exception("创建交互 Agent 失败")
            return RunResult(ok=False, outcome="failed", error=str(e))

        self._emit(
            "agent",
            policy.intro
            + f"模型：{req.resolved.provider_name}/{req.resolved.model_id}\n"
            + policy.capabilities,
            {"phase": "hint"},
        )

        # 仅空 checkpoint 用时间线补充背景；后续轮次沿用历史消息（含压缩摘要）。
        values = {}
        try:
            state = agent.get_state(config)
            values = getattr(state, "values", None) or {}
            has_history = bool(values.get("messages")) if isinstance(values, dict) else False
        except Exception:
            logger.exception("读取对话 checkpoint 失败，跳过时间线摘录")
            has_history = True
        self._context_injected = has_history
        recent = (
            self._recent_events_digest(req.project_root, limit=40)
            if policy.timeline and not has_history else ""
        )
        question, metadata, update = policy.prepare_question(
            question, getattr(req, "design_context", ""), values, has_history
        )
        if update:
            self._emit("user", update)
        sent_note = f"（发送时间：{_now()}）"
        user_content = f"{question}\n\n{sent_note}"
        if recent:
            user_content = (
                f"{question}\n\n{sent_note}\n\n"
                "——\n【近期时间线摘录（供参考，回答不必复述全文）】\n"
                f"{recent}"
            )
        user_content = _attachment_content(user_content, req.attachments)
        user_message = {"role": "user", "content": user_content}
        if metadata:
            # 元数据不进入模型文本；每轮保留版本，跟随 checkpoint 的提交与生命周期。
            user_message["additional_kwargs"] = metadata

        try:
            early = self._run_agent_turn(
                agent,
                config,
                seen_msg_ids,
                req,
                payload={"messages": [user_message]},
                first=True,
                allow_auto_lesson=False,
                start_hint="Agent 处理中…",
            )
            if early:
                # 对话模式：取消/失败原样返回；审批等待也返回
                return early
            if self._cancel.is_set():
                return self._cancelled_result(req, False)

            final_text = ""
            try:
                state = agent.get_state(config)
                values = getattr(state, "values", None) or {}
                messages = values.get("messages") if isinstance(values, dict) else None
                if messages:
                    # 只取最终文本；流式阶段已写入时间线，禁止把 checkpoint 全量历史再刷一遍
                    final_text = _extract_text(list(messages))
            except Exception:
                pass

            self._emit("info", "本轮回复已完成")
            return RunResult(ok=True, outcome="success", final_text=final_text)
        except StepLimitExceeded as e:
            err = str(e)
            self._emit("error", err, {"outcome": "incomplete", "max_steps": e.limit})
            return RunResult(ok=False, outcome="incomplete", error=err)
        except Exception as e:
            logger.exception("交互失败")
            err = _format_engine_error(e)
            self._emit("error", f"交互失败：{err}")
            return RunResult(ok=False, outcome="failed", error=err)

    @staticmethod
    def _recent_events_digest(project_root: Path, *, limit: int = 40) -> str:
        try:
            from wokbee.core.memory import SessionMemory
            memory = SessionMemory(project_root)
            if memory.records():
                return memory.read(recent=1)
            from wokbee.core.context_usage import (
                events_as_messages,
                load_context_state,
            )
            from wokbee.core.models import ProjectEvent
            from wokbee.core.paths import events_path
            from tokbee.core import context_manager as ctxman

            ep = events_path(project_root)
            if not ep.exists():
                return ""
            events: list = []
            with ep.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(ProjectEvent.from_dict(json.loads(line)))
                    except (json.JSONDecodeError, TypeError, KeyError):
                        continue
            # 只取最新一轮，避免旧轮日志灌进上下文
            events = slice_latest_round(events)
            messages = events_as_messages(events)
            state = load_context_state(project_root)
            summary, active, _ = ctxman.slice_after_compaction(
                messages, state.get("compaction_points") or [],
            )
            rows: list[str] = []
            if summary:
                body = summary.strip().replace("\n", " ")
                if len(body) > 400:
                    body = body[:400] + "…"
                rows.append(f"- [summary] {body}")
            for msg in active:
                kind = msg.get("kind") or msg.get("role") or "?"
                body = (msg.get("content") or "").strip().replace("\n", " ")
                if not body:
                    continue
                if len(body) > 220:
                    body = body[:220] + "…"
                rows.append(f"- [{kind}] {body}")
            if not rows:
                return ""
            return "\n".join(rows[-limit:])
        except Exception:
            logger.exception("读取近期时间线失败")
            return ""

    @memory_turn
    def run(self, req: RunRequest, *, resume: bool = False) -> RunResult:
        policy = mode_policy(req.runner_mode or "run")
        if not policy.pipeline:
            return self._run_conversation(req, policy)
        self._event_log.reset()
        self._context_injected = False
        self._experience_updated_by_tool = False
        thread_id = f"wokbee-{req.project.id}"
        self._configure_step_budget(req)
        config = self._graph_config(thread_id, req)
        seen_msg_ids: set[str] = set()

        base_message = (
            req.user_message.strip()
            or req.project.goal
            or "请根据项目目标推进工作。"
        )
        base_content = _attachment_content(base_message, req.attachments)

        try:
            # 非 resume 必须清空 checkpoint，否则会继承上次空 AIMessage / 半截计划而秒退
            if not resume:
                _reset_run_state(req.project.id)
            agent = self.build_agent(req, mode=policy.name)
        except Exception as e:
            logger.exception("创建 Agent 失败")
            return RunResult(ok=False, outcome="failed", error=str(e))

        self._emit(
            "agent",
            f"引擎已启动（Deep Agents + 联网工具）。"
            f"模型：{req.resolved.provider_name}/{req.resolved.model_id}\n"
            f"策略：{req.approval.summary()}；Agent 工作目录：{req.project_root}\n"
            "可用：web_search / http_get / http_request / 文件工具 / execute\n"
            "执行策略：已有 pipeline.json 时按 steps 顺序一路执行——"
            "script 步骤自动执行（不耗 Token）；ai 步骤执行已确定的业务任务（按需调 LLM，"
            "不重新规划）；仅当脚本报错 / 数据异常 / 输出不符预期时才异常接管。",
            {"phase": "hint"},
        )

        final_text = ""
        trajectory_messages: list = []
        current_phase_index: int | None = None
        current_phase_type = ""

        try:
            if resume:
                early = self._run_agent_turn(
                    agent,
                    config,
                    seen_msg_ids,
                    req,
                    payload={
                        "messages": [
                            {
                                "role": "user",
                                "content": "请继续未完成的流程（审批后或中断后续）。",
                            }
                        ]
                    },
                    first=True,
                )
                if early:
                    self._finalize_early_failure(req, early, agent, config)
                    return early
            else:
                # ── 有序管线：按 pipeline.json steps 顺序逐步推进 ──
                phase_idx = 0
                # 仅携带脚本阶段产出；AI 阶段结果已经写入 checkpoint 消息历史。
                script_context_parts: list[str] = []
                ai_turn = 0
                max_phases = max(
                    1,
                    int(
                        getattr(getattr(self, "settings", None), "max_pipeline_phases", 64)
                        or 64
                    ),
                )

                self._emit(
                    "info",
                    "按 scripts/pipeline.json 的 steps 顺序推进"
                    "（script 自动执行不耗 Token；ai 步骤执行固定业务任务；"
                    f"阶段上限 {max_phases}，与 Agent 共用运行步数上限 {req.max_steps}）…",
                )

                for _ in range(max_phases):
                    if self._cancel.is_set():
                        return self._cancelled_result(req, True)
                    pipe = run_pipeline_until_ai_or_end(
                        req.project_root,
                        start_phase=phase_idx,
                        prior_context=script_context_parts,
                        cancel_event=self._cancel,
                        step_budget=self._step_budget,
                    )

                    if not pipe.ran or not pipe.phases:
                        self._emit("info", f"未使用有序管线：{pipe.reason}")
                        early = self._run_agent_turn(
                            agent,
                            config,
                            seen_msg_ids,
                            req,
                            payload={
                                "messages": [
                                    {"role": "user", "content": base_content}
                                ]
                            },
                            first=True,
                        )
                        if early:
                            self._finalize_early_failure(req, early, agent, config)
                            return early
                        break

                    if pipe.items:
                        self._emit(
                            "info",
                            f"有序管线：{pipe.reason}",
                            {
                                "phase": pipe.next_phase_index,
                                "need_ai": pipe.need_ai,
                                "ok": pipe.ok,
                            },
                        )
                        self._emit_script_items(pipe.items)

                    failed_phase = next(
                        (phase for phase in pipe.phase_results if not phase.ok),
                        None,
                    )
                    for phase in pipe.phase_results:
                        if phase.ok:
                            self._record_phase_state(
                                phase_index=phase.index,
                                phase_type="script",
                                status="success",
                            )

                    current_phase_index = (
                        failed_phase.index
                        if failed_phase is not None
                        else pipe.next_phase_index if pipe.ai_steps else None
                    )
                    current_phase_type = (
                        "script" if failed_phase is not None else "ai"
                    ) if current_phase_index is not None else ""

                    script_context_parts = list(pipe.context_parts or [])

                    if pipe.ok and not pipe.need_ai:
                        # 全部为 script 步骤且成功：0 Token 完成（无 ai 步骤的纯脚本管线）
                        published: list[str] = []
                        try:
                            from wokbee.engine.script_factory import goal_wants_deliverables

                            if goal_wants_deliverables(req.project.goal or base_message):
                                published = publish_pipeline_outputs(req.project_root)
                        except Exception:
                            logger.exception("发布纯脚本管线产物失败")
                        self._emit(
                            "agent",
                            "有序管线均为脚本且已成功，0 Token 完成。\n"
                            + (
                                "脚本原始产物已复制到 deliverables/："
                                + ", ".join(published[:20])
                                if published
                                else "保留脚本原始输出格式，未强制转换为 Markdown。"
                            ),
                        )
                        self._emit("info", "运行结束：成功（纯脚本有序管线，未调用 LLM）")
                        return RunResult(
                            ok=True,
                            outcome="success",
                            final_text=(pipe.combined_output or "")[:2000],
                            lesson_id="",
                        )

                    user_message = build_user_message_for_ai_phase(
                        original_message=base_message,
                        pipeline=pipe,
                    )
                    ai_turn += 1
                    early = self._run_agent_turn(
                        agent,
                        config,
                        seen_msg_ids,
                        req,
                        payload={
                            "messages": [
                                {"role": "user", "content": user_message}
                            ]
                        },
                        first=True,
                    )
                    if early:
                        if failed_phase is not None:
                            self._record_phase_state(
                                phase_index=failed_phase.index,
                                phase_type="script",
                                status="失败-AI接管后失败",
                                detail=failed_phase.error,
                            )
                        else:
                            self._record_phase_state(
                                phase_index=pipe.next_phase_index,
                                phase_type="ai",
                                status="失败-AI接管后失败",
                                detail=early.error or early.outcome,
                            )
                        self._finalize_early_failure(req, early, agent, config)
                        return early

                    if failed_phase is not None:
                        self._record_phase_state(
                            phase_index=failed_phase.index,
                            phase_type="script",
                            status="失败-AI接管后成功",
                            detail="AI 已完成当前异常接管",
                        )
                    else:
                        self._record_phase_state(
                            phase_index=pipe.next_phase_index,
                            phase_type="ai",
                            status="success",
                        )
                    current_phase_index = None
                    current_phase_type = ""

                    segment_text = ""
                    try:
                        state = agent.get_state(config)
                        values = getattr(state, "values", None) or {}
                        messages = (
                            values.get("messages") if isinstance(values, dict) else None
                        )
                        if messages:
                            trajectory_messages = list(messages)
                            # 流式已写时间线；此处只取文本，避免把历史 messages 重复落盘
                            segment_text = _extract_text(trajectory_messages)
                            final_text = segment_text
                    except Exception:
                        pass

                    # 不把 AI 产出复制进后续 user 消息；Agent 可从 checkpoint 历史读取它。
                    # 本轮 user 已经带过脚本上下文，后续 AI 阶段也从 checkpoint 读取。
                    script_context_parts = []

                    phase_idx = pipe.next_phase_index + 1
                    if phase_idx >= len(pipe.phases):
                        break
                else:
                    err = f"有序管线阶段次数达到上限 max_phases={max_phases}，执行未完成"
                    self._emit(
                        "error",
                        err,
                        {"outcome": "incomplete", "max_phases": max_phases},
                    )
                    lesson = self._ensure_lesson_written(
                        req,
                        "partial",
                        "执行未完成：有序管线阶段次数达到上限",
                        err,
                        success_path=build_success_path_from_messages(trajectory_messages),
                    )
                    return RunResult(
                        ok=False,
                        outcome="incomplete",
                        error=err,
                        final_text=final_text,
                        lesson_id=lesson.id if lesson else "",
                    )

            # 收尾：再取一次最终文本（不重复刷时间线）
            try:
                state = agent.get_state(config)
                values = getattr(state, "values", None) or {}
                messages = values.get("messages") if isinstance(values, dict) else None
                if messages:
                    trajectory_messages = list(messages)
                    final_text = _extract_text(trajectory_messages) or final_text
            except Exception:
                pass

            if self._cancel.is_set():
                return self._cancelled_result(req, True)

            success_path = build_success_path_from_messages(trajectory_messages)
            lesson = self._ensure_lesson_written(
                req,
                "success",
                final_text[:800] or "任务执行完成",
                "",
                success_path=success_path,
            )
            self._emit("info", "运行结束：成功")
            return RunResult(
                ok=True,
                outcome="success",
                final_text=final_text,
                lesson_id=lesson.id if lesson else "",
            )

        except StepLimitExceeded as e:
            logger.warning("Agent 达到 max_steps 硬上限：%s", e)
            err = str(e)
            self._emit("error", err, {"outcome": "incomplete", "max_steps": e.limit})
            if current_phase_index is not None:
                self._record_phase_state(
                    phase_index=current_phase_index,
                    phase_type=current_phase_type or "ai",
                    status="失败-AI接管后失败",
                    detail=err,
                )
            fail_path = ""
            try:
                state = agent.get_state(config)
                values = getattr(state, "values", None) or {}
                messages = values.get("messages") if isinstance(values, dict) else None
                if messages:
                    fail_path = build_success_path_from_messages(list(messages))
            except Exception:
                pass
            lesson = self._ensure_lesson_written(
                req,
                "partial",
                "执行未完成：已达到 max_steps 硬上限",
                err,
                success_path=fail_path,
            )
            return RunResult(
                ok=False,
                outcome="incomplete",
                error=err,
                lesson_id=lesson.id if lesson else "",
            )
        except Exception as e:
            logger.exception("Agent 运行失败")
            err = _format_engine_error(e)
            self._emit("error", f"执行失败：{err}")
            if current_phase_index is not None:
                self._record_phase_state(
                    phase_index=current_phase_index,
                    phase_type=current_phase_type or "ai",
                    status="失败-AI接管后失败",
                    detail=err,
                )
            fail_path = ""
            try:
                state = agent.get_state(config)
                values = getattr(state, "values", None) or {}
                messages = values.get("messages") if isinstance(values, dict) else None
                if messages:
                    fail_path = build_success_path_from_messages(list(messages))
            except Exception:
                pass
            lesson = self._ensure_lesson_written(
                req,
                "failed",
                str(e)[:500],
                str(e),
                success_path=fail_path,
            )
            return RunResult(
                ok=False,
                outcome="failed",
                error=err,
                lesson_id=lesson.id if lesson else "",
            )
