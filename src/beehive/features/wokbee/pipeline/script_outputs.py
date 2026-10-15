"""按职责组织的 script_outputs。"""
from __future__ import annotations
import shutil
import time
from pathlib import Path


def _persist_script_callback(project_root: Path, script_rel: str, body: str) -> str | None:
    """把脚本 stdout/stderr 落到项目根目录 script_callback_*.md（主机兜底）。"""
    from datetime import datetime

    text = (body or "").strip()
    if not text:
        return None
    root = Path(project_root)
    stem = Path(script_rel or "script").stem or "script"
    # 自动固化名较长时，尽量用可读尾段
    if "_ai_" in stem:
        stem = stem.split("_ai_", 1)[-1] or stem
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    content = (
        f"# 脚本 callback：{stem}\n\n"
        f"- 生成时间：{stamp}\n"
        f"- 脚本：{script_rel}\n\n"
        f"---\n\n"
        f"{text}\n"
    )
    out = root / f"script_callback_{stem}.md"
    try:
        out.write_text(content, encoding="utf-8")
    except OSError:
        return None
    try:
        return out.relative_to(root).as_posix()
    except ValueError:
        return str(out)


def publish_pipeline_outputs(project_root: Path, *, recent_minutes: int = 15) -> list[str]:
    """收集项目根目录最近产生的顶层文件，复制到 output，保留原始格式。"""
    root = Path(project_root)
    target = root / "output"
    target.mkdir(parents=True, exist_ok=True)
    published: list[str] = []
    infra = {
        ".git", ".vscode", "__pycache__", "memory", "uploads",
        "output", ".project", "scripts", "workspace", "runs", "archives",
    }

    def copy_to(src: Path, rel: Path) -> None:
        try:
            dst = target / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            published.append(rel.as_posix())
        except OSError:
            pass

    cutoff = time.time() - max(1, int(recent_minutes)) * 60
    top_level = root.iterdir() if root.exists() else []
    for path in top_level:
        # 只收集顶层普通文件；基础设施目录及目录项一律排除
        if not path.is_file() or path.name.lower() in infra:
            continue
        name = path.name.lower()
        if name in ("readme.txt", "project.json"):
            continue
        if path.name.startswith("script_callback_") and path.suffix.lower() == ".md":
            continue
        try:
            if path.stat().st_mtime >= cutoff:
                copy_to(path, Path(path.name))
        except OSError:
            continue
    return published
