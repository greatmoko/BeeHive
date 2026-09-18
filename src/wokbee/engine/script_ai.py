"""解析 AI 提案并固化 AI 手写脚本。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tokbee.core.safe_io import safe_write_text
from wokbee.engine.script_events import ScriptStep
from wokbee.core.paths import ensure_project_layout, scripts_dir

def apply_ai_authored_scripts(
    project_root: Path,
    *,
    lesson_id: str,
    project_id: str = "",
    script_files: list[dict[str, Any]] | None,
) -> list[ScriptStep]:
    """把总结 AI 手写的脚本写入 scripts/，并合并进 pipeline.json。

    命名规范：`项目ID_脚本作用(≤4词)_时间戳.扩展名`；同秒同名冲突时追加序号，
    绝不覆盖已有脚本。返回成功写入且纳入管线的 ScriptStep 列表。
    """
    from wokbee.engine.script_runner import load_pipeline
    from wokbee.engine import script_pipeline as legacy
    safe_write_text = legacy.safe_write_text
    ensure_project_layout = legacy.ensure_project_layout
    scripts_dir = legacy.scripts_dir
    make_script_name = legacy.make_script_name
    sanitize_ai_script_filename = legacy.sanitize_ai_script_filename

    files = script_files or []
    if not files:
        return []

    ensure_project_layout(project_root)
    sdir = scripts_dir(project_root)
    sdir.mkdir(parents=True, exist_ok=True)
    pid = project_id or Path(project_root).name

    written: list[ScriptStep] = []
    pipeline_entries: list[dict[str, Any]] = []

    for i, item in enumerate(files, 1):
        if not isinstance(item, dict):
            continue
        src_fname = sanitize_ai_script_filename(str(item.get("filename") or ""))
        content = str(item.get("content") or "")
        if not src_fname or not content.strip():
            continue
        uploaded = (Path(project_root) / "uploads" / src_fname).resolve()
        if uploaded.is_file():
            rel = uploaded.relative_to(Path(project_root).resolve()).as_posix()
            desc = str(item.get("description") or "").strip() or f"直接运行上传脚本：{rel}"
            step = ScriptStep(
                tool="script",
                args={"source": "uploaded_script", "path": rel},
                description=desc[:200],
                rel_path=rel,
            )
            written.append(step)
            if bool(item.get("in_pipeline", True)):
                pipeline_entries.append(
                    {
                        "id": f"script_upload_{i}",
                        "type": "script",
                        "path": rel,
                        "tool": "script",
                        "description": desc[:200],
                        "args": {},
                    }
                )
            continue
        ext = Path(src_fname).suffix.lower() or ".py"
        purpose = str(item.get("description") or "").strip() or Path(src_fname).stem
        fname = make_script_name(pid, purpose, ext)
        target = sdir / fname
        n = 2
        while target.exists():
            try:
                old = target.read_text(encoding="utf-8")
            except OSError:
                old = ""
            if old.strip() == content.strip():
                break
            fname = f"{Path(fname).stem}_{n}{ext}"
            target = sdir / fname
            n += 1

        body = content
        if ext in {".bat", ".cmd"}:
            body = body.replace("\r\n", "\n").replace("\n", "\r\n")
        safe_write_text(target, body)

        rel = f"scripts/{fname}"
        desc = str(item.get("description") or "").strip() or f"AI 手写脚本 {fname}"
        step = ScriptStep(
            tool="ai_authored",
            args={"filename": fname, "source": "ai_summary", "_src": src_fname},
            description=desc[:200],
            rel_path=rel,
        )
        written.append(step)
        if bool(item.get("in_pipeline", True)):
            pipeline_entries.append(
                {
                    "id": f"script_ai_{i}",
                    "type": "script",
                    "path": rel,
                    "tool": "ai_authored",
                    "description": f"获取数据：{desc}"[:200],
                    "args": {"filename": fname, "source": "ai_summary"},
                }
            )

    if not written:
        return []

    # 合并 pipeline：AI 脚本插到首个 AI 步骤之前；已有同 path 则跳过
    data = load_pipeline(project_root) or {
        "version": 2,
        "lesson_id": lesson_id,
        "goal": "",
        "steps": [],
        "scripts": [],
        "ai_steps": [],
        "policy": {
            "ordered_execution": True,
            "invoke_ai_on_script_error": True,
            "invoke_ai_on_bad_data": True,
            "scripts_not_archived": True,
        },
    }
    steps = list(data.get("steps") or []) if isinstance(data.get("steps"), list) else []
    existing_paths = {
        str(s.get("path") or "")
        for s in steps
        if isinstance(s, dict) and s.get("type") == "script"
    }
    insert_at = next(
        (idx for idx, s in enumerate(steps) if isinstance(s, dict) and s.get("type") == "ai"),
        len(steps),
    )
    added = 0
    for entry in pipeline_entries:
        path = str(entry.get("path") or "")
        if not path or path in existing_paths:
            continue
        steps.insert(insert_at + added, entry)
        existing_paths.add(path)
        added += 1

    # 同步 scripts 兼容字段
    scripts_compat = [
        {
            "path": s.rel_path,
            "tool": s.tool,
            "description": s.description,
            "args": s.args,
        }
        for s in written
    ]
    old_scripts = data.get("scripts") if isinstance(data.get("scripts"), list) else []
    seen_script_paths = {
        str(x.get("path") or "") for x in old_scripts if isinstance(x, dict)
    }
    merged_scripts = list(old_scripts)
    for sc in scripts_compat:
        if sc["path"] not in seen_script_paths:
            merged_scripts.append(sc)
            seen_script_paths.add(sc["path"])

    data["steps"] = steps
    data["scripts"] = merged_scripts
    data["lesson_id"] = data.get("lesson_id") or lesson_id
    data["version"] = int(data.get("version") or 2)
    # 附注：记录 AI 手写
    policy = data.get("policy") if isinstance(data.get("policy"), dict) else {}
    policy["ai_authored_scripts"] = True
    data["policy"] = policy

    safe_write_text(
        sdir / "pipeline.json",
        json.dumps(data, ensure_ascii=False, indent=2),
    )
    return written
