"""Pipeline.json 写入与校验。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from beehive.common.io.safe_io import safe_write_text
from beehive.features.wokbee.projects.paths import ensure_project_layout, scripts_dir

from beehive.features.wokbee.pipeline.pipeline_proposal import normalize_pipeline_steps
from beehive.features.wokbee.pipeline.script_policy import goal_wants_deliverables, goal_no_check
from beehive.features.wokbee.pipeline.script_names import make_script_name
from beehive.features.wokbee.pipeline.script_publish import _render_publish_script
from beehive.features.wokbee.pipeline.validation import format_order_markdown, load_pipeline, validate_pipeline_script_paths



def apply_ai_pipeline_steps(
    project_root: Path,
    *,
    lesson_id: str,
    goal: str = "",
    pipeline_steps: list[dict[str, Any]] | None,
    rename_map: dict[str, str] | None = None,
) -> bool:
    """若总结 AI 给出了 pipeline_steps，则以该顺序覆盖 pipeline.json 的 steps。

    steps 可同时含 `script` 与 `ai`：
    - script：确定性/机械步骤，后续直接执行（不耗 Token）；
    - ai：**已经确定的 AI 业务任务**（理解/分析/整理/创意/写作等），后续运行照样调用 LLM
      执行该固定任务，但禁止重新规划整个 Pipeline。
    过滤掉「AI 读取 Skill / 思考下一步 / 决定调用什么工具」这类 Agent 内部思考过程。
    不生成任何 final_ai。rename_map：{AI 原始文件名 → 规范化后的 memory/ 文件名}。
    返回是否成功应用。
    """

    steps_in = pipeline_steps or []
    if not steps_in:
        return False

    ensure_project_layout(project_root)
    sdir = scripts_dir(project_root)
    sdir.mkdir(parents=True, exist_ok=True)
    root = Path(project_root)

    proposal = normalize_pipeline_steps(root, sdir, steps_in, rename_map)
    if proposal is None:
        return False
    normalized, missing_script_paths = proposal

    # pipeline 是系统的执行事实来源，绝不落盘无法执行的脚本引用。
    # 整条 AI 提案只要含幽灵脚本就拒绝，调用方可继续使用本轮真实固化的 pipeline。
    if missing_script_paths:
        return False

    if not normalized:
        return False

    # 目标要求交付到 output/ 但 AI 未给出发布步骤时，补一个确定性发布步骤
    if goal_wants_deliverables(goal) and not any(
        s.get("type") == "script" and str(s.get("tool") or "").lower() == "publish"
        for s in normalized
    ):
        _base = make_script_name(Path(project_root).name, "publish_deliverables")
        pub_name = _base
        _n = 2
        while (sdir / pub_name).exists():
            pub_name = f"{Path(_base).stem}_{_n}.py"
            _n += 1
        safe_write_text(sdir / pub_name, _render_publish_script())
        normalized.append(
            {
                "id": f"script_publish",
                "type": "script",
                "path": f"memory/{pub_name}",
                "tool": "publish",
                "description": "发布：把脚本实际产出文件复制到 output/",
                "args": {},
            }
        )

    previous_pipeline = load_pipeline(project_root)
    data = previous_pipeline or {
        "version": 3,
        "scripts": [],
        "ai_steps": [],
        "policy": {},
    }
    data["version"] = 3
    data["lesson_id"] = lesson_id or data.get("lesson_id") or ""
    if goal:
        data["goal"] = goal
    data["steps"] = normalized
    data["scripts"] = [
        {
            "path": s.get("path"),
            "tool": s.get("tool"),
            "description": s.get("description"),
            "args": s.get("args") or {},
        }
        for s in normalized
        if s.get("type") == "script"
    ]
    data["ai_steps"] = [
        {
            "description": s.get("description"),
            "prompt_hint": s.get("prompt_hint") or "",
        }
        for s in normalized
        if s.get("type") == "ai"
    ]
    data.pop("final_ai", None)
    policy = data.get("policy") if isinstance(data.get("policy"), dict) else {}
    policy.update(
        {
            "ordered_execution": True,
            "invoke_ai_on_script_error": True,
            "invoke_ai_on_bad_data": not goal_no_check(goal),
            "scripts_not_archived": True,
            "order_source": "ai_summary",
            "ai_steps_in_pipeline": True,
            "ai_intervention": "ai_steps_and_error_recovery",
        }
    )
    data["policy"] = policy
    data["order_markdown"] = format_order_markdown(normalized)

    if validate_pipeline_script_paths(root, normalized):
        return False
    safe_write_text(
        sdir / "pipeline.json",
        json.dumps(data, ensure_ascii=False, indent=2),
    )
    return True




# 清理时会处理/忽略的脚本扩展名（pipeline.json 另作保留）
