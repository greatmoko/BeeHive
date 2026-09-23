"""经验摘要：从运行时间线提炼可复用的成功路径。"""

from __future__ import annotations

def build_success_path_from_timeline_events(
    events: list,
    *,
    limit: int = 40,
) -> tuple[str, str, str]:
    """从时间线事件提炼 (success_path, summary, errors) — 作 AI 回退用。"""
    steps: list[str] = []
    agent_bits: list[str] = []
    errors: list[str] = []
    for ev in events or []:
        kind = getattr(ev, "kind", "") or ""
        content = (getattr(ev, "content", None) or "").strip()
        if not content:
            continue
        if kind == "tool":
            if len(steps) >= limit:
                continue
            line = content
            for prefix in (
                "call: ",
                "callback: ",
                "⟶ 调用工具：",
                "⟵ ",
            ):
                if line.startswith(prefix):
                    line = line[len(prefix) :].strip()
                    break
            if len(line) > 280:
                line = line[:280] + "…"
            if "本地脚本" in line and "成功" in line:
                line = re.split(r"成功[:：]", line, maxsplit=1)[0] + "成功"
            steps.append(f"{len(steps) + 1}. {line}")
        elif kind == "agent":
            agent_bits.append(content[:400])
        elif kind == "error":
            errors.append(content[:400])
        elif kind == "info" and ("失败" in content or "取消" in content):
            errors.append(content[:400])

    success_path = "\n".join(steps) if steps else ""
    summary = ""
    if steps:
        summary = f"本轮记录了 {len(steps)} 个工具/脚本相关流程步骤（不含结果正文）。"
    elif agent_bits:
        summary = "本轮以 Agent 过程说明为主，详见注意事项与实现步骤。"
    err_text = "\n".join(errors[-5:]) if errors else ""
    return success_path, summary, err_text


# --------------------------------------------------------------------------- #
# Agent 直接写入经验（工具路径）：Agent 用一手上下文直接落盘，不再跑独立总结大调用
# --------------------------------------------------------------------------- #



from pathlib import Path
from typing import Any

from wokbee.engine.lesson_models import Lesson
from wokbee.engine.lesson_pipeline import sync_lesson_from_pipeline
from wokbee.engine.lesson_storage import LessonStore

def merge_pipeline_steps(
    ai_steps: list[dict[str, Any]],
    solid_steps: list[Any],
    ai_written: list[Any],
) -> list[dict[str, Any]]:
    """合并「轨迹固化脚本」「AI 手写脚本」与 AI 给出的 pipeline_steps。

    AI 引用的 script 路径若在磁盘上不存在，尝试按文件名匹配轨迹固化或
    AI 手写产生的真实脚本；仍匹配不上时由 pipeline 固化层拒绝整条提案。
    AI 明确给出 pipeline_steps 时完全尊重其顺序，不再把未覆盖脚本前插，避免
    改变无 pipeline 探索出的真实脚本/AI 交错路径。AI 未给清单时才使用轨迹顺序。
    """
    merged: list[dict[str, Any]] = []
    for raw in ai_steps or []:
        if not isinstance(raw, dict):
            continue
        merged.append(dict(raw))

    real_names: dict[str, str] = {}  # 小写文件名 → 规范化 scripts/ 路径
    for st in list(solid_steps or []) + list(ai_written or []):
        rel = str(getattr(st, "rel_path", "") or "")
        if rel:
            real_names[Path(rel).name.lower()] = rel

    covered: set[str] = set()
    for step in merged:
        if str(step.get("type") or "").lower() != "script":
            continue
        path = str(step.get("path") or "").replace("\\", "/").strip()
        base = Path(path).name.lower() if path else ""
        if base and base in real_names:
            step["path"] = real_names[base]  # AI 引用名对齐真实脚本
        covered.add(base)
        covered.add(str(step.get("path") or "").replace("\\", "/").strip().lower())

    if merged:
        return merged
    return [
        {
            "type": "script",
            "path": getattr(st, "rel_path", "") or "",
            "tool": getattr(st, "tool", "") or "script",
            "description": (getattr(st, "description", "") or "轨迹固化脚本")[:200],
            "args": getattr(st, "args", {}) or {},
        }
        for st in list(solid_steps or [])
        if getattr(st, "rel_path", "")
    ]


def solidify_lesson_pipeline(
    project_root: Path,
    lesson: Lesson,
    *,
    success_path: str,
    events: list | None = None,
    script_files: list[dict] | None = None,
    pipeline_steps: list[dict] | None = None,
) -> tuple[bool, int]:
    """两条经验写入路径共用；显式提案通过校验前不改写 pipeline。"""
    from wokbee.engine.script_factory import (
        apply_ai_authored_scripts,
        apply_ai_pipeline_steps,
        solidify_scripts,
    )

    has_proposal = bool(pipeline_steps)
    solid = solidify_scripts(
        project_root,
        lesson_id=lesson.id,
        goal=lesson.goal,
        summary=lesson.summary,
        success_path=success_path,
        events=events,
        write_pipeline=not has_proposal,
    )
    ai_written = apply_ai_authored_scripts(
        project_root,
        lesson_id=lesson.id,
        project_id=lesson.project_id,
        script_files=script_files or [],
        write_pipeline=not has_proposal,
    )
    applied = False
    if has_proposal:
        rename_map = {
            str(step.args["_src"]): Path(step.rel_path).name
            for step in ai_written
            if step.args.get("_src")
        }
        applied = apply_ai_pipeline_steps(
            project_root,
            lesson_id=lesson.id,
            goal=lesson.goal,
            pipeline_steps=merge_pipeline_steps(pipeline_steps, solid.script_steps, ai_written),
            rename_map=rename_map or None,
        )
        if not applied:
            raise ValueError(
                "pipeline_steps 校验失败，原 pipeline 和经验未更新。"
                "请确认脚本路径存在，或在 script_files 中提供完整源码后重试。"
            )
    sync_lesson_from_pipeline(lesson, project_root)
    return applied, len(ai_written)


def write_ai_lesson(
    project_root: Path,
    *,
    project_id: str,
    goal: str,
    outcome: str = "success",
    summary: str,
    success_path: str = "",
    notes: str = "",
    errors: str = "",
    script_files: list[dict[str, Any]] | None = None,
    pipeline_steps: list[dict[str, Any]] | None = None,
    used_skills: list[str] | None = None,
    reference_materials: list[dict[str, Any]] | None = None,
    model_label: str = "",
    policy: str = "",
    events: list[Any] | None = None,
) -> Lesson:
    """把 Agent 给出的经验内容直接固化为经验文档 + pipeline + 脚本（不调 LLM）。

    复用与结束总结完全相同的固化管线（solidify/AI 手写脚本/pipeline/Skills 快照/
    参考材料登记），保证后续运行可严格照章执行。管线固化失败时不写入经验。
    """
    from wokbee.core.references import snapshot_used_skills as _snap_skills
    from wokbee.core.references import write_reference_manifest as _write_manifest

    root = Path(project_root)
    store = LessonStore(root)
    lesson = Lesson(
        project_id=project_id,
        goal=goal or "",
        outcome=outcome or "success",
        summary=(summary or "").strip() or "本轮流程经验（方法向，不含结果/产物）。",
        success_path=(success_path or "").strip(),
        notes=(notes or "").strip(),
        errors=(errors or "").strip(),
        model=model_label,
        policy=policy,
    )

    solidify_lesson_pipeline(
        root,
        lesson,
        success_path=lesson.success_path,
        events=list(events or []),
        script_files=script_files,
        pipeline_steps=pipeline_steps,
    )

    try:
        written = _snap_skills(root, [s for s in (used_skills or []) if str(s).strip()])
        mats = [
            m
            for m in (reference_materials or [])
            if isinstance(m, dict)
            and (str(m.get("path") or "").strip() or str(m.get("note") or "").strip())
        ]
        manifest_path = _write_manifest(
            root,
            used_skills=[str(s) for s in (used_skills or []) if str(s).strip()],
            materials=mats,
            goal=lesson.goal,
        )
        _ = written, manifest_path
    except Exception:
        import logging

        logging.getLogger("wokbee").exception("update_project_experience：保存参考材料失败")

    store.save(lesson)
    return lesson
