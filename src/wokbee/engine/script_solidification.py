"""根据运行轨迹固化脚本与初始 Pipeline。"""

from __future__ import annotations

import json
from pathlib import Path

from tokbee.core.safe_io import safe_write_text
from wokbee.engine.script_events import ScriptStep, SolidifyResult


def solidify_scripts(
    project_root: Path,
    *,
    lesson_id: str,
    goal: str = "",
    summary: str = "",
    success_path: str = "",
    events: list | None = None,
) -> SolidifyResult:
    """根据轨迹固化脚本引用与有序 pipeline.json（按 steps 顺序，非强制交错）。"""

    from wokbee.engine.script_runner import format_order_markdown
    from wokbee.engine import script_pipeline as legacy

    safe_write_text = legacy.safe_write_text
    ensure_project_layout = legacy.ensure_project_layout
    scripts_dir = legacy.scripts_dir
    extract_scriptable_from_events = legacy.extract_scriptable_from_events
    extract_scriptable_from_path_text = legacy.extract_scriptable_from_path_text
    _project_script_path_from_command = legacy._project_script_path_from_command
    _script_tokens = legacy._script_tokens
    _render_script = legacy._render_script
    _render_publish_script = legacy._render_publish_script
    goal_wants_deliverables = legacy.goal_wants_deliverables
    events_touched_deliverables = legacy.events_touched_deliverables
    goal_no_check = legacy.goal_no_check
    make_script_name = legacy.make_script_name

    ensure_project_layout(project_root)
    sdir = scripts_dir(project_root)
    sdir.mkdir(parents=True, exist_ok=True)

    scriptable = extract_scriptable_from_events(events or [])
    from_path = extract_scriptable_from_path_text(success_path)
    # 合并：事件优先，路径轨迹补漏（避免 AI 散文覆盖后丢工具）
    seen_keys = {
        f"{s.tool}:{json.dumps(s.args, ensure_ascii=False, sort_keys=True)}"
        for s in scriptable
    }
    for s in from_path:
        key = f"{s.tool}:{json.dumps(s.args, ensure_ascii=False, sort_keys=True)}"
        if key not in seen_keys:
            seen_keys.add(key)
            scriptable.append(s)

    written: list[ScriptStep] = []
    ordered_steps: list[dict] = []
    project_id = Path(project_root).name

    def _unique_fname(purpose: str, ext: str = ".py") -> str:
        """规范命名 + 同秒同名冲突时追加序号，绝不覆盖已有脚本。"""
        base = make_script_name(project_id, purpose, ext)
        cand = sdir / base
        n = 2
        while cand.exists():
            stem = Path(base).stem
            cand = sdir / f"{stem}_{n}{ext}"
            n += 1
        return cand.name

    direct_upload_paths: set[str] = set()
    for step in scriptable:
        if step.tool == "execute":
            direct = _project_script_path_from_command(
                project_root,
                str((step.args or {}).get("command") or ""),
                "uploads",
            )
            if direct:
                direct_upload_paths.add(direct.lower())

    for i, step in enumerate(scriptable, 1):
        if step.tool == "execute":
            command = str((step.args or {}).get("command") or "")
            direct = _project_script_path_from_command(project_root, command, "uploads")
            if direct:
                if direct.lower() in {s.rel_path.lower() for s in written}:
                    continue
                step.rel_path = direct
                step.args = {"source": "uploaded_script", "path": direct}
                step.description = f"直接运行上传脚本：{direct}"
                written.append(step)
                ordered_steps.append(
                    {
                        "id": f"script_{i}",
                        "type": "script",
                        "path": direct,
                        "tool": "script",
                        "description": step.description,
                        "args": {},
                    }
                )
                continue

            generated = _project_script_path_from_command(project_root, command, "scripts")
            has_scripts_ref = any(
                "/scripts/" in token.replace("\\", "/").lower()
                or token.replace("\\", "/").lower().startswith("scripts/")
                for token in _script_tokens(command)
            )
            if direct_upload_paths and (generated or has_scripts_ref):
                # 已识别到上传脚本时，忽略旧的 scripts/ 包装脚本，避免脚本套娃。
                continue

        src = _render_script(step)
        if not src:
            continue
        if step.tool == "execute":
            label = str((step.args or {}).get("label") or "execute")
        else:
            label = step.tool
        fname = _unique_fname(label)
        path = sdir / fname
        safe_write_text(path, src)
        step.rel_path = f"scripts/{fname}"
        written.append(step)
        ordered_steps.append(
            {
                "id": f"script_{i}",
                "type": "script",
                "path": step.rel_path,
                "tool": step.tool,
                "description": f"获取数据：{step.description}"[:200],
                "args": step.args,
            }
        )

    # 默认顺序：全部数据脚本（真实执行序）→ 可选确定性发布步骤
    # 注意：本路径（规则/无模型回退）只固化确定性 script 步骤；ai 步骤由总结 AI 通过
    # apply_ai_pipeline_steps 按需并入（明确的业务任务，如分析/整理/撰写）。不生成 final_ai。

    # 发布步骤：仅当目标明确要求产物进 deliverables/，或本次执行确实写过 deliverables/ 时才补
    if written and (goal_wants_deliverables(goal) or events_touched_deliverables(events)):
        pub_name = _unique_fname("publish_deliverables")
        pub_path = sdir / pub_name
        pub_src = _render_publish_script()
        safe_write_text(pub_path, pub_src)
        ordered_steps.append(
            {
                "id": f"script_publish",
                "type": "script",
                "path": f"scripts/{pub_name}",
                "tool": "publish",
                "description": "发布：把脚本实际产出文件复制到 deliverables/",
                "args": {},
            }
        )
        written.append(
            ScriptStep(
                tool="publish",
                args={},
                description="publish deliverables",
                rel_path=f"scripts/{pub_name}",
            )
        )

    order_md = format_order_markdown(ordered_steps)
    pipeline = {
        "version": 3,
        "lesson_id": lesson_id,
        "goal": goal,
        "steps": ordered_steps,
        "order_markdown": order_md,
        # 兼容旧字段（新策略：script 步骤来自固化，ai 步骤由总结 AI 并入）
        "scripts": [
            {
                "path": s.rel_path,
                "tool": s.tool,
                "description": s.description,
                "args": s.args,
            }
            for s in written
        ],
        "ai_steps": [],
        "policy": {
            "ordered_execution": True,
            "invoke_ai_on_script_error": True,
            # 目标明确「不校验/不检查/不验证」时不允许因数据/内容异常唤醒 AI
            "invoke_ai_on_bad_data": not goal_no_check(goal),
            "scripts_not_archived": True,
            # 策略：script 步骤自动执行；ai 步骤为已确定的业务任务（由总结 AI 并入时生效）
            "ai_steps_in_pipeline": True,
            "ai_intervention": "ai_steps_and_error_recovery",
        },
    }
    safe_write_text(
        sdir / "pipeline.json",
        json.dumps(pipeline, ensure_ascii=False, indent=2),
    )
    if written:
        script_md = "\n".join(
            f"- `{s.rel_path}` — {s.description}" for s in written
        )
        script_md += (
            "\n\n约定：每个脚本执行后把 callback 写入 "
            "`workspace/script_callback_<脚本名>.md`；"
            "需要交付的文件由发布脚本复制到 deliverables/（保留原始文件，不合并成 final.md）。"
            "执行顺序见 `scripts/pipeline.json` 的 steps。"
        )
    else:
        script_md = "（本轮无可固化脚本。）"

    return SolidifyResult(
        script_steps=written,
        pipeline_rel="scripts/pipeline.json",
        script_section_md=script_md,
        order_section_md=order_md,
    )
