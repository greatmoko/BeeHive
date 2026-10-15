"""纯脚本产物先发布，再由 AI 对照原目标进行验收。"""
from __future__ import annotations
from beehive.system.prompts import script_acceptance_prompt
from beehive.features.wokbee.execution.runner_models import RunResult
from beehive.features.wokbee.pipeline.script_outputs import publish_pipeline_outputs
import logging

logger = logging.getLogger("wokbee")


def verify_script_outputs(self, req, run_state, pipe):
    published: list[str] = []
    try:
        from beehive.features.wokbee.pipeline.script_policy import goal_wants_deliverables

        if goal_wants_deliverables(req.project.goal or run_state.base_message):
            published = publish_pipeline_outputs(req.project_root)
    except Exception:
        logger.exception("发布纯脚本管线产物失败")
    self._emit(
        "agent",
        "有序管线均为脚本且已成功，准备由 AI 核对产出物是否达到目标。\n"
        + (
            "脚本原始产物已复制到 output/："
            + ", ".join(published[:20])
            if published
            else "保留脚本原始输出格式，未强制转换为 Markdown。"
        ),
    )
    verification = self._run_agent_turn(
        run_state.agent,
        run_state.config,
        run_state.seen_msg_ids,
        req,
        payload={"messages": [{"role": "user", "content": (
            script_acceptance_prompt(run_state.base_message, pipe.combined_output)
        )}]},
        first=True,
        allow_auto_lesson=False,
        start_hint="脚本已完成，AI 正在核对产出物…",
    )
    if verification is not None:
        return verification
    self._emit("info", "运行结束：脚本产出已完成 AI 核对")
    return RunResult(
        ok=True,
        outcome="success",
        final_text=(pipe.combined_output or "")[:2000],
        lesson_id="",
    )
