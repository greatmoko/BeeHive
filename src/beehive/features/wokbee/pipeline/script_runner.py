"""本地执行经验固化脚本，并支持按 pipeline steps 有序推进（script/ai 任意组合）。"""

from __future__ import annotations

import json
import logging
import re
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from beehive.features.wokbee.projects.paths import scripts_dir
from beehive.features.wokbee.execution.archive_guard import command_touches_archives
from beehive.common.process.subprocess_util import run_cancellable
from beehive.system.runtime.runtime_env_cache import collect_runtime_env
from beehive.system.runtime.runtime_env_format import enrich_shell_env

logger = logging.getLogger("wokbee")

EventFn = Callable[[str, str, dict], None]


from beehive.features.wokbee.pipeline.models import ScriptRunItem


from beehive.features.wokbee.pipeline.models import PhaseResult


from beehive.features.wokbee.pipeline.models import PipelineRunResult


from beehive.features.wokbee.pipeline import validation


from beehive.features.wokbee.pipeline.validation import resolve_pipeline_script_path


from beehive.features.wokbee.pipeline.validation import validate_pipeline_script_paths


from beehive.features.wokbee.pipeline.validation import normalize_steps


from beehive.features.wokbee.pipeline.validation import group_phases


from beehive.features.wokbee.pipeline.script_execution import _persist_script_callback


from beehive.features.wokbee.pipeline.script_execution import publish_pipeline_outputs


from beehive.features.wokbee.pipeline.script_execution import _script_touches_archives


from beehive.features.wokbee.pipeline.script_execution import run_one_script


from beehive.features.wokbee.pipeline.script_execution import run_script_phase


def peek_pipeline(project_root: Path) -> PipelineRunResult:
    """读取并规范化管线，不执行。

    steps 可同时含 `script` 与 `ai`：script 直接执行，ai 是已确定的业务任务（仍调 LLM，
    但不重新规划）。不生成任何 final_ai。
    """
    root = Path(project_root)
    pipe_path = scripts_dir(root) / "pipeline.json"
    data = validation.load_pipeline(root)
    result = PipelineRunResult(pipeline_path=pipe_path if pipe_path.exists() else None)
    if not data:
        result.reason = "无 pipeline.json，走完整 AI 流程"
        result.need_ai = True
        return result

    steps = normalize_steps(data)
    result.steps = steps
    result.phases = group_phases(steps)
    result.ran = True
    if not steps:
        result.reason = "pipeline 无步骤"
        result.need_ai = True
        result.ok = True
        return result
    result.reason = f"有序管线共 {len(steps)} 步（逐步执行，不合并相邻步骤）"
    result.need_ai = any(s.get("type") == "ai" for s in steps)
    result.ok = True
    return result


def run_pipeline_until_ai_or_end(
    project_root: Path,
    *,
    start_phase: int = 0,
    timeout_sec: int = 120,
    prior_context: list[str] | None = None,
    cancel_event: threading.Event | None = None,
    step_budget=None,
) -> PipelineRunResult:
    """从 start_phase 起执行脚本；遇到 AI 步骤则暂停并 need_ai。

    若整段均为脚本且成功，则 need_ai=False。
    """
    result = peek_pipeline(project_root)
    if not result.ran or not result.phases:
        return result

    missing = validate_pipeline_script_paths(project_root, result.steps)
    if missing:
        first_missing = next(
            (
                i
                for i, step in enumerate(result.steps)
                if isinstance(step, dict)
                and str(step.get("type") or "").lower() == "script"
                and str(step.get("path") or "").replace("\\", "/").strip()
                in missing
            ),
            0,
        )
        bad = result.steps[first_missing] if result.steps else {}
        item = ScriptRunItem(
            path=str(bad.get("path") or missing[0]),
            ok=False,
            error="pipeline 引用了不存在或越界的脚本：" + "、".join(missing),
            description=str(bad.get("description") or "脚本路径检查"),
            step_id=str(bad.get("id") or ""),
        )
        phase = PhaseResult(
            type="script",
            ok=False,
            items=[item],
            error=item.error,
            index=first_missing,
        )
        result.phase_results.append(phase)
        result.items = [item]
        result.next_phase_index = first_missing
        result.error_summary = item.error
        result.need_ai = True
        result.reason = (
            f"管线第 {first_missing + 1} 步脚本路径检查失败，已暂停；"
            "请先修复 pipeline 脚本引用，再进行异常接管"
        )
        return result

    context = list(prior_context or [])
    result.context_parts = context
    all_items: list[ScriptRunItem] = []

    i = start_phase
    while i < len(result.phases):
        phase = result.phases[i]
        if phase["type"] == "script":
            if step_budget is not None:
                step_budget.consume("pipeline_script")
            pr = run_script_phase(
                project_root, phase["steps"],
                timeout_sec=timeout_sec, cancel_event=cancel_event,
            )
            pr.index = i
            result.phase_results.append(pr)
            all_items.extend(pr.items)
            if pr.output:
                context.append(
                    f"## 阶段 {i+1}（脚本）\n{pr.output}"
                )
            if not pr.ok:
                result.items = all_items
                result.ok = False
                result.combined_output = "\n\n".join(context)
                result.context_parts = context
                result.error_summary = "\n".join(
                    f"- `{it.path}`: {it.error or '失败'}" for it in pr.items if not it.ok
                )
                result.need_ai = True
                result.next_phase_index = i
                result.ai_steps = []  # 本阶段失败，交给 AI 补救本步
                result.reason = (
                    f"管线第 {i+1} 步（脚本）失败，暂停；"
                    "请 AI 补救后再继续后续步骤"
                )
                return result
            i += 1
            continue

        # AI 步骤：暂停，把当前业务任务交给模型
        result.items = all_items
        result.ok = True
        result.combined_output = "\n\n".join(context)
        result.context_parts = context
        result.next_phase_index = i
        result.ai_steps = list(phase["steps"])
        result.need_ai = True
        # 后续还有什么（告知 AI 不要越权做后面的脚本）
        remaining = result.phases[i + 1 :]
        tail = ""
        if remaining:
            tail = "；本阶段完成后主机将继续执行后续脚本/AI 阶段，请勿越权执行后续脚本步骤"
        result.reason = f"按管线进入第 {i+1} 步（AI）{tail}"
        return result

    # 全部阶段完成（script/ai 均已完成）
    result.items = all_items
    result.ok = True
    result.combined_output = "\n\n".join(context)
    result.context_parts = context
    result.next_phase_index = len(result.phases)
    result.need_ai = False
    result.ai_steps = []
    result.reason = "有序管线全部阶段执行完毕，结束"
    return result


from beehive.features.wokbee.pipeline.ai_context import build_user_message_for_ai_phase


from beehive.features.wokbee.pipeline.validation import format_order_markdown
