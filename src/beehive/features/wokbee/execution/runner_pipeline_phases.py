"""按原有序管线推进阶段，保留接管、预算与状态更新。"""
from __future__ import annotations
from beehive.features.wokbee.execution.runner_models import RunResult
from beehive.features.wokbee.pipeline.ai_context import build_user_message_for_ai_phase
from beehive.features.wokbee.pipeline.script_runner import run_pipeline_until_ai_or_end
from beehive.system.agent.message_content import _extract_text
from beehive.features.wokbee.execution.runner_support import build_success_path_from_messages
from beehive.features.wokbee.execution.runner_script_acceptance import verify_script_outputs
import logging

logger = logging.getLogger("wokbee")


def advance_pipeline(self, req, run_state):
    phase_idx = 0
    # 仅携带脚本阶段产出；AI 阶段结果已经写入 checkpoint 消息历史。
    script_context_parts: list[str] = []
    max_phases = max(
        1,
        int(
            getattr(getattr(self, "settings", None), "max_pipeline_phases", 64)
            or 64
        ),
    )

    self._emit(
        "info",
        "按 memory/pipeline.json 的 steps 顺序推进"
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
                run_state.agent,
                run_state.config,
                run_state.seen_msg_ids,
                req,
                payload={
                    "messages": [
                        {"role": "user", "content": run_state.base_content}
                    ]
                },
                first=True,
            )
            if early:
                self._finalize_early_failure(req, early, run_state.agent, run_state.config)
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

        run_state.current_phase_index = (
            failed_phase.index
            if failed_phase is not None
            else pipe.next_phase_index if pipe.ai_steps else None
        )
        run_state.current_phase_type = (
            "script" if failed_phase is not None else "ai"
        ) if run_state.current_phase_index is not None else ""

        script_context_parts = list(pipe.context_parts or [])

        if pipe.ok and not pipe.need_ai:
            # Script steps still require an AI acceptance pass so the
            # produced files are checked against the user's goal.
            return verify_script_outputs(self, req, run_state, pipe)

        user_message = build_user_message_for_ai_phase(
            original_message=run_state.base_message,
            pipeline=pipe,
        )
        early = self._run_agent_turn(
            run_state.agent,
            run_state.config,
            run_state.seen_msg_ids,
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
            self._finalize_early_failure(req, early, run_state.agent, run_state.config)
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
        run_state.current_phase_index = None
        run_state.current_phase_type = ""

        segment_text = ""
        try:
            state = run_state.agent.get_state(run_state.config)
            values = getattr(state, "values", None) or {}
            messages = (
                values.get("messages") if isinstance(values, dict) else None
            )
            if messages:
                run_state.trajectory_messages = list(messages)
                # 流式已写时间线；此处只取文本，避免把历史 messages 重复落盘
                segment_text = _extract_text(run_state.trajectory_messages)
                run_state.final_text = segment_text
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
            success_path=build_success_path_from_messages(run_state.trajectory_messages),
        )
        return RunResult(
            ok=False,
            outcome="incomplete",
            error=err,
            final_text=run_state.final_text,
            lesson_id=lesson.id if lesson else "",
        )
