"""按职责组织的 ai_context。"""
from __future__ import annotations
from beehive.features.wokbee.pipeline.models import PipelineRunResult


def build_user_message_for_ai_phase(
    *,
    original_message: str,
    pipeline: PipelineRunResult,
    phase_index: int | None = None,
) -> str:
    """构造 AI 步骤/异常接管时的用户消息（含先前脚本上下文与本阶段任务）。

    - ai 步骤：pipeline 固化好的**明确业务任务**（description + prompt_hint），
      后续运行直接执行该固定任务，不重新规划整个 Pipeline；
    - 脚本/步骤失败：AI 接管处理当前异常并完成剩余目标。
    """
    idx = phase_index if phase_index is not None else pipeline.next_phase_index
    if pipeline.ok and pipeline.ai_steps:
        # 正常的 ai 步骤（已确定的业务任务，非自由规划）
        parts = [
            original_message.strip() or "请根据项目目标推进工作。",
            "",
            "【有序执行管线 — AI 步骤】",
            f"说明：{pipeline.reason}",
            "请**只执行下面列出的 AI 业务任务**：不要重新规划整个 Pipeline，"
            "不要自行添加目标里没有的任务；如需验证当前输入，可以重复执行之前的脚本，"
            "但不得越权执行后续步骤。",
        ]
        if pipeline.context_parts or pipeline.combined_output:
            parts.extend(
                [
                    "",
                    "## 此前脚本阶段已产出的上下文（callback 等）",
                    (pipeline.combined_output or "\n\n".join(pipeline.context_parts))[:8000],
                ]
            )
        parts.extend(["", f"## 本阶段 AI 任务（步骤 {idx+1}）"])
        for n, step in enumerate(pipeline.ai_steps, 1):
            if isinstance(step, dict):
                parts.append(f"{n}. {step.get('description') or step}")
                hint = step.get("prompt_hint") or ""
                if hint:
                    parts.append(f"   （{hint}）")
            else:
                parts.append(f"{n}. {step}")
        parts.append(
            "请先读取项目根目录 script_callback_*.md 中的脚本 callback（若有）；"
            "完成后把中间文件写在项目根目录（最终交付写入 output/）；"
            "用户上传文件在 uploads/；同名或相近文件以最新修改时间为准。"
        )
    else:
        # 异常接管：脚本/步骤失败
        parts = [
            original_message.strip() or "请根据项目目标推进工作。",
            "",
            "【有序执行管线 — 异常接管】",
            f"说明：{pipeline.reason}",
            "请只处理当前异常并修复当前步骤；不要执行后续步骤。"
            "允许重复执行之前的脚本来验证修复结果。"
            "如需调整管线，只提交当前失败步骤的最小修正版，"
            "并通过 update_project_experience 固化，不要包办后续阶段。",
        ]
        if pipeline.context_parts or pipeline.combined_output:
            parts.extend(
                [
                    "",
                    "## 此前阶段已产出的上下文（脚本结果等）",
                    (pipeline.combined_output or "\n\n".join(pipeline.context_parts))[:8000],
                ]
            )
        if pipeline.error_summary:
            parts.extend(
                [
                    "",
                    "## 脚本失败（请先补救）",
                    pipeline.error_summary,
                    "补救后把关键结果写入项目根目录 script_callback_*.md，主机将按经验顺序继续后续步骤。",
                ]
            )

    # 预告后续阶段，避免 AI 包办
    remaining = []
    for ph in pipeline.phases[idx + 1 :]:
        labels = []
        for s in ph.get("steps") or []:
            labels.append(str(s.get("description") or s.get("path") or s.get("type")))
        remaining.append(f"- [{ph.get('type')}] " + "；".join(labels))
    if remaining:
        parts.extend(
            [
                "",
                "## 后续阶段（由主机按顺序执行，你现在不要做）",
                *remaining,
            ]
        )
    return "\n".join(parts)
