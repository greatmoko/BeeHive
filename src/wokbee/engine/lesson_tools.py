"""构造 Agent 可调用的经验写入工具。"""

from __future__ import annotations

from pathlib import Path


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
    from wokbee.engine.script_factory import drop_missing_pipeline_scripts

    @tool
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
        """把本轮可复用的方法经验固化写入项目经验（memory/experiences/），并更新 pipeline.json 与 scripts/。

        必须调用的场景：
        - 首次运行（尚无 pipeline.json/经验）收尾时：把本次验证过的流程固化为经验与管线；
        - 运行中脚本报错/数据异常被你修复后：把修正方法写入经验，并修正 pipeline/脚本，供下次稳定复跑；
        - 你发现已有经验/管线明显过时或有更优方法时。

        字段要求（后续运行会**严格照章执行**，务必只写真实验证过、可复用、尽量精简的内容）：
        - summary: 经验摘要（概要介绍经验作用，非结果正文）
        - success_path: 有序成功步骤，每步格式 `序号. 执行角色: "{执行内容}"; 【步骤说明】`
          （执行角色=AI/工具调用/脚本执行/系统执行；剔除失败/试错步骤）
        - notes: 注意事项，无序列表，每条 `- **问题**：解决办法`
        - outcome: success | failed
        - script_files: 需固化的脚本 [{"filename","content","description","in_pipeline"}]——
          **每个可复用命令/脚本都要在这里给出完整源码**（.py/.bat/.ps1 等），没有则省略
        - pipeline_steps: 有序步骤 [{"type":"script","path":"uploads/..." 或 "scripts/...","description"} 或
          {"type":"ai","description":"明确业务任务","prompt_hint":"..."}]，无则省略。
          **每个 script 步骤的 path 必须对应真实脚本文件**（来自 uploads/、script_files 或 scripts/ 已有文件），
          且必须是 `scripts/...` 或 `uploads/...` 相对虚拟路径；系统写入后会重新读取并验证，失败则拒绝保存。
        - used_skills: 用到的全局 Skill 目录名；reference_materials: 需存 uploads/references/ 的材料
        """
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
        try:
            dropped = drop_missing_pipeline_scripts(project_root)
        except Exception:
            import logging

            logging.getLogger("wokbee").exception("清理幽灵管线脚本失败")
            dropped = []
        if dropped:
            warn = (
                "警告：以下管线步骤引用的脚本文件不存在，已从 pipeline.json 移除："
                + "、".join(dropped)
                + "。若这些步骤必要，请重新调用本工具：在 script_files 中给出每个脚本的"
                "完整源码（filename+content），并在 pipeline_steps 里引用对应文件名。"
            )
            _notify("info", warn)
            result += "\n" + warn
        _notify(
            "lesson",
            f"Agent 已固化项目经验：`{rel}`"
            + (f"（脚本 {len(lesson.scripts)} 个已入管线）" if lesson.scripts else ""),
            {"lesson_id": lesson.id, "path": str(Path(project_root) / rel)},
        )
        return result

    return [update_project_experience]
