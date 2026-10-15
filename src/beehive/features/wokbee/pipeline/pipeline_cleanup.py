"""缺失管线脚本清理与旧脚本可逆归档。"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from beehive.common.io.safe_io import safe_write_text
from beehive.features.wokbee.projects.paths import ensure_project_layout, scripts_dir
import shutil
from datetime import datetime
from beehive.features.wokbee.projects.paths import archives_dir
def drop_missing_pipeline_scripts(project_root: Path) -> list[str]:
    """移除 pipeline.json 中引用不存在脚本文件的 script 步骤，返回被移除的路径。

    经验写入（尤其工具路径 AI 未给 script_files 时）可能产出引用幽灵脚本的管线；
    写入后立即清理，保证下次运行不会因「文件不存在」中断。仅当确有移除时才重写文件。
    """
    from beehive.features.wokbee.pipeline.validation import format_order_markdown
    from beehive.features.wokbee.pipeline.validation import load_pipeline
    from beehive.features.wokbee.pipeline.validation import resolve_pipeline_script_path

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

_QUARANTINE_EXTENSIONS = frozenset(
    {".py", ".bat", ".cmd", ".ps1", ".json", ".sh", ".js", ".vbs"}
)

def quarantine_obsolete_scripts(
    project_root: Path,
    *,
    kept_paths: list[str],
    lesson_id: str = "",
) -> tuple[list[str], Path]:
    """把 memory/ 顶层中不在 kept_paths 里的脚本移入 archives/discard_<ts>/。

    kept_paths 形如 "memory/foo.py"，按文件名匹配；pipeline.json 与子目录不处理。
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
            dest = archives_dir(project_root) / f"discard_{stamp}"
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
