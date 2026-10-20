"""解析 AI 提案并把 AI 手写脚本固化进 memory/。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from beehive.common.io.safe_io import safe_write_text
from beehive.features.wokbee.pipeline.script_models import ScriptStep
from beehive.features.wokbee.projects.paths import ensure_project_layout, scripts_dir

from beehive.features.wokbee.pipeline.script_names import make_script_name, sanitize_ai_script_filename

def apply_ai_authored_scripts(
    project_root: Path,
    *,
    lesson_id: str,
    project_id: str = "",
    script_files: list[dict[str, Any]] | None,
) -> list[ScriptStep]:
    """把总结 AI 手写的脚本写入 memory/。

    命名规范：`项目ID_脚本作用(≤4词)_时间戳.扩展名`；同秒同名冲突时追加序号，
    绝不覆盖已有脚本。返回成功写入的 ScriptStep 列表。
    """

    files = script_files or []
    if not files:
        return []

    ensure_project_layout(project_root)
    sdir = scripts_dir(project_root)
    sdir.mkdir(parents=True, exist_ok=True)
    pid = project_id or Path(project_root).name

    written: list[ScriptStep] = []

    for item in files:
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
            written.append(
                ScriptStep(
                    tool="script",
                    args={"source": "uploaded_script", "path": rel},
                    description=desc[:200],
                    rel_path=rel,
                )
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

        rel = f"memory/{fname}"
        desc = str(item.get("description") or "").strip() or f"AI 手写脚本 {fname}"
        written.append(
            ScriptStep(
                tool="ai_authored",
                args={"filename": fname, "source": "ai_summary", "_src": src_fname},
                description=desc[:200],
                rel_path=rel,
            )
        )

    return written
