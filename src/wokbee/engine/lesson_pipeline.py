"""从最终 scripts/pipeline.json 同步经验字段。"""

from __future__ import annotations

from pathlib import Path

from wokbee.engine.lesson_models import Lesson

def build_success_path_from_pipeline(steps: list[dict] | None) -> str:
    """把最终 pipeline 渲染成人和 AI 可读的成功路径。

    pipeline 是执行顺序的唯一事实来源；经验只负责解释同一组步骤，不能再独立
    从运行轨迹推导另一套顺序。
    """
    lines: list[str] = []
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        kind = str(step.get("type") or "").lower().strip()
        description = " ".join(str(step.get("description") or "").split())[:300]
        if kind == "script":
            path = str(step.get("path") or "").replace("\\", "/").strip()
            if not path:
                continue
            tool = str(step.get("tool") or "script").strip()
            action = f"脚本: {path}"
            if tool and tool != "script":
                action = f"脚本: {path}; 工具: {tool}"
            lines.append(
                f'{len(lines) + 1}. 脚本执行: "{{{action}}}"; '
                f"【{description or '执行确定性脚本步骤'}】"
            )
        elif kind == "ai":
            hint = " ".join(str(step.get("prompt_hint") or "").split())[:300]
            action = f"业务任务: {description or '执行已确定的 AI 业务任务'}"
            if hint:
                action += f"; 提示: {hint}"
            lines.append(
                f'{len(lines) + 1}. AI: "{{{action}}}"; '
                f"【{description or '执行已确定的 AI 业务任务'}】"
            )
    return "\n".join(lines)


def sync_lesson_from_pipeline(lesson: Lesson, project_root: Path) -> bool:
    """从最终 pipeline 同步经验的执行主线和脚本清单。"""
    from wokbee.engine.script_runner import load_pipeline

    data = load_pipeline(Path(project_root))
    steps = data.get("steps") if isinstance(data, dict) else None
    if not isinstance(steps, list):
        return False
    lesson.success_path = build_success_path_from_pipeline(steps)
    lesson.scripts = [
        str(step.get("path") or "").replace("\\", "/").strip()
        for step in steps
        if isinstance(step, dict)
        and str(step.get("type") or "").lower() == "script"
        and str(step.get("path") or "").strip()
    ]
    lesson.pipeline = "scripts/pipeline.json"
    return True


