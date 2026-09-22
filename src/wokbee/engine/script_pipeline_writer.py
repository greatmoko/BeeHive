"""Pipeline.json 写入与校验。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tokbee.core.safe_io import safe_write_text
from wokbee.core.paths import ensure_project_layout, scripts_dir

def _resolve_pipeline_script_path(
    root: Path,
    sdir: Path,
    raw_path: str,
    *,
    rename_map: dict[str, str] | None = None,
) -> str | None:
    """解析 AI 脚本引用，只接受项目 scripts/ 或 uploads/ 相对路径。"""
    from wokbee.engine.script_runner import resolve_pipeline_script_path

    raw = (raw_path or "").replace("\\", "/").strip()
    if not raw:
        return None
    mapped = {
        Path(str(k).replace("\\", "/")).name.lower(): Path(
            str(v).replace("\\", "/")
        ).name
        for k, v in (rename_map or {}).items()
        if str(k).strip() and str(v).strip()
    }
    parts = raw.split("/")
    if len(parts) < 2 or parts[0].lower() not in {"scripts", "uploads"}:
        return None
    base = mapped.get(parts[-1].lower(), parts[-1])
    candidate_rel = "/".join(parts[:-1] + [base])
    candidate = resolve_pipeline_script_path(root, candidate_rel)
    if candidate is None:
        return None
    try:
        return candidate.relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return None


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
    不生成任何 final_ai。rename_map：{AI 原始文件名 → 规范化后的 scripts/ 文件名}。
    返回是否成功应用。
    """
    from wokbee.engine import script_pipeline as legacy
    safe_write_text = legacy.safe_write_text
    ensure_project_layout = legacy.ensure_project_layout
    scripts_dir = legacy.scripts_dir
    goal_wants_deliverables = legacy.goal_wants_deliverables
    goal_no_check = legacy.goal_no_check
    make_script_name = legacy.make_script_name
    _render_publish_script = legacy._render_publish_script
    from wokbee.engine.script_runner import (
        format_order_markdown,
        load_pipeline,
        validate_pipeline_script_paths,
    )

    steps_in = pipeline_steps or []
    if not steps_in:
        return False

    ensure_project_layout(project_root)
    sdir = scripts_dir(project_root)
    sdir.mkdir(parents=True, exist_ok=True)
    root = Path(project_root)

    normalized: list[dict[str, Any]] = []
    missing_script_paths: list[str] = []
    for i, raw in enumerate(steps_in):
        if not isinstance(raw, dict):
            continue
        t = str(raw.get("type") or "").lower().strip()
        if t not in ("script", "ai"):
            continue
        if t == "script":
            path = str(raw.get("path") or "").replace("\\", "/").strip()
            if not path:
                return False
            resolved_path = _resolve_pipeline_script_path(
                root,
                sdir,
                path,
                rename_map=rename_map,
            )
            if not resolved_path:
                missing_script_paths.append(path)
                continue
            path = resolved_path
            step = {
                "id": str(raw.get("id") or f"script_{i+1}"),
                "type": "script",
                "path": path,
                "tool": str(raw.get("tool") or "script"),
                "description": str(raw.get("description") or path)[:200],
                "args": raw.get("args") if isinstance(raw.get("args"), dict) else {},
            }
        else:
            desc = str(raw.get("description") or "").strip()
            if not desc:
                continue
            # 过滤 Agent 内部思考过程：思考/决定下一步/规划/自由探索等不是业务任务
            low = desc.lower()
            if any(
                k in low
                for k in ("思考下一步", "决定下一步", "规划下一步", "自由决定", "自由探索", "读取skill", "读取 skill", "决定调用什么工具")
            ):
                continue
            step = {
                "id": str(raw.get("id") or f"ai_{i+1}"),
                "type": "ai",
                "description": desc[:300],
                "prompt_hint": str(raw.get("prompt_hint") or raw.get("hint") or "").strip(),
            }
        normalized.append(step)

    # pipeline 是系统的执行事实来源，绝不落盘无法执行的脚本引用。
    # 整条 AI 提案只要含幽灵脚本就拒绝，调用方可继续使用本轮真实固化的 pipeline。
    if missing_script_paths:
        return False

    if not normalized:
        return False

    # 目标要求交付到 deliverables/ 但 AI 未给出发布步骤时，补一个确定性发布步骤
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
                "path": f"scripts/{pub_name}",
                "tool": "publish",
                "description": "发布：把脚本实际产出文件复制到 deliverables/",
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


def drop_missing_pipeline_scripts(project_root: Path) -> list[str]:
    """移除 pipeline.json 中引用不存在脚本文件的 script 步骤，返回被移除的路径。

    经验写入（尤其工具路径 AI 未给 script_files 时）可能产出引用幽灵脚本的管线；
    写入后立即清理，保证下次运行不会因「文件不存在」中断。仅当确有移除时才重写文件。
    """
    from wokbee.engine.script_runner import (
        format_order_markdown,
        load_pipeline,
        resolve_pipeline_script_path,
    )

    root = Path(project_root)
    data = load_pipeline(root)
    if not data:
        return []
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        return []
    kept: list[dict[str, Any]] = []
    dropped: list[str] = []
    for s in steps:
        if not isinstance(s, dict) or str(s.get("type") or "").lower() != "script":
            kept.append(s)
            continue
        rel = str(s.get("path") or "").replace("\\", "/").strip()
        if rel and resolve_pipeline_script_path(root, rel) is not None:
            kept.append(s)
        else:
            dropped.append(rel or "（空路径步骤）")
    if not dropped:
        return []
    data["steps"] = kept
    data["scripts"] = [
        {
            "path": s.get("path"),
            "tool": s.get("tool"),
            "description": s.get("description"),
            "args": s.get("args") or {},
        }
        for s in kept
        if isinstance(s, dict) and s.get("type") == "script"
    ]
    data["ai_steps"] = [
        {
            "description": s.get("description"),
            "prompt_hint": s.get("prompt_hint") or "",
        }
        for s in kept
        if isinstance(s, dict) and s.get("type") == "ai"
    ]
    data["order_markdown"] = format_order_markdown(kept)
    safe_write_text(
        scripts_dir(root) / "pipeline.json",
        json.dumps(data, ensure_ascii=False, indent=2),
    )
    return dropped


# 清理时会处理/忽略的脚本扩展名（pipeline.json 另作保留）
_QUARANTINE_EXTENSIONS = frozenset(
    {".py", ".bat", ".cmd", ".ps1", ".json", ".sh", ".js", ".vbs"}
)


def quarantine_obsolete_scripts(
    project_root: Path,
    *,
    kept_paths: list[str],
    lesson_id: str = "",
) -> tuple[list[str], Path]:
    """把 scripts/ 顶层中不在 kept_paths 里的脚本移入 archives/discard_<ts>/scripts/。

    kept_paths 形如 "scripts/foo.py"，按文件名匹配；pipeline.json 与子目录不处理。
    「下次运行只执行 pipeline.json steps[].path」，因此保留 kept 之外的脚本即可安全孤立。
    返回 (已移走文件名列表, 目标目录)。可逆：不删除，只是搬到归档下。
    """
    ensure_project_layout(Path(project_root))
    sdir = scripts_dir(project_root)
    if not sdir.exists():
        return [], Path()

    kept_names = {Path(p).name for p in kept_paths if p}
    moved: list[str] = []
    dest: Path | None = None

    for p in sorted(sdir.iterdir(), key=lambda x: x.name):
        if not p.is_file():
            continue
        if p.name == "pipeline.json" or p.name in kept_names:
            continue
        ext = p.suffix.lower()
        if ext not in _QUARANTINE_EXTENSIONS:
            continue
        if dest is None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = archives_dir(project_root) / f"discard_{stamp}" / "scripts"
            dest.mkdir(parents=True, exist_ok=True)
        try:
            target = dest / p.name
            if target.exists():
                target = dest / f"{p.stem}_{lesson_id[-4:] or 'x'}{p.suffix}"
            shutil.move(str(p), str(target))
            moved.append(p.name)
        except OSError:
            continue
    return moved, dest or Path()
