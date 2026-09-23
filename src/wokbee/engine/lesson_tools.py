"""构造 Agent 可调用的经验写入工具。"""

from __future__ import annotations

from pathlib import Path

from sysprompt import EXPERIENCE_TOOL_DESCRIPTION


def build_experience_tools(
    *,
    project_id: str,
    project_root: Path,
    goal: str = "",
    model_label: str = "",
    policy: str = "",
    emit=None,
    on_written=None,
    events_provider=None,
):
    """构造经验写入工具：update_project_experience（Agent 直接固化经验/管线/脚本）。

    on_written: 工具成功写入后回调（无参），runner 用它标记「本轮经验已由 Agent 更新」，
    结束兜底据此跳过自动总结。
    events_provider: 返回本轮运行事件快照的可调用对象；solidify 需要真实执行轨迹
    才能识别已跑过的命令；用户上传脚本优先直接引用 uploads/，不复制到项目根目录，
    其他可复用命令才固化成 scripts/ 下的脚本文件——缺失时 AI 又未给 script_files，
    pipeline 就会引用幽灵脚本，二次运行报「文件不存在」。
    """

    def _notify(kind: str, content: str, meta: dict | None = None) -> None:
        if emit:
            try:
                emit(kind, content, meta or {})
            except Exception:
                pass

    from wokbee.engine import lesson_service as legacy
    write_ai_lesson = legacy.write_ai_lesson
    from langchain_core.tools import tool

    @tool(description=EXPERIENCE_TOOL_DESCRIPTION)
    def update_project_experience(
        summary: str,
        success_path: str = "",
        notes: str = "",
        outcome: str = "success",
        errors: str = "",
        script_files: list[dict] | None = None,
        pipeline_steps: list[dict] | None = None,
        used_skills: list[str] | None = None,
        reference_materials: list[dict] | None = None,
    ) -> str:
        """写入项目经验、脚本与执行管线。"""
        if not (summary or "").strip():
            return "错误：summary 不能为空"
        tool_events = None
        if callable(events_provider):
            try:
                tool_events = events_provider() or None
            except Exception:
                tool_events = None
        try:
            lesson = write_ai_lesson(
                project_root,
                project_id=project_id,
                goal=goal,
                outcome=outcome if outcome in ("success", "failed") else "success",
                summary=summary,
                success_path=success_path,
                notes=notes,
                errors=errors,
                script_files=list(script_files or []),
                pipeline_steps=list(pipeline_steps or []),
                used_skills=list(used_skills or []),
                reference_materials=list(reference_materials or []),
                model_label=model_label,
                policy=policy,
                events=tool_events,
            )
        except Exception as e:
            _notify("error", f"写入项目经验失败：{e}")
            return f"错误：写入项目经验失败：{e}"
        if on_written:
            try:
                on_written()
            except Exception:
                pass
        rel = lesson.filename or "memory/experiences/"
        result = (
            f"项目经验已写入 `{rel}`"
            + (f"，固化脚本 {len(lesson.scripts)} 个" if lesson.scripts else "")
            + "；pipeline.json 已更新，下次运行按 steps 执行。"
        )
        _notify(
            "lesson",
            f"Agent 已固化项目经验：`{rel}`"
            + (f"（脚本 {len(lesson.scripts)} 个已入管线）" if lesson.scripts else ""),
            {"lesson_id": lesson.id, "path": str(Path(project_root) / rel)},
        )
        return result

    return [update_project_experience]
