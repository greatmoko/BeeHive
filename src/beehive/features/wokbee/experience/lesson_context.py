"""经验总结的环境参数与既有脚本上下文。"""
from __future__ import annotations
from pathlib import Path
from beehive.features.wokbee.projects.paths import memory_dir, uploads_dir


def build_environment_block(
    *,
    model: str = "",
    policy: str = "",
    project_root: str = "",
    extra: str = "",
) -> str:
    """经验总结用运行环境块（与 Agent 会话上下文同源）。"""
    from beehive.system.runtime.runtime_env_format import build_runtime_env_block

    return build_runtime_env_block(
        project_root=project_root,
        model=model,
        policy=policy,
        extra=extra,
    )

def collect_scripts_context(project_root: Path, *, max_chars: int = 12000) -> str:
    """收集 memory/pipeline.json 与 memory/、uploads/ 下脚本源码摘要，供 AI 总结。"""
    root = Path(project_root)
    mdir = memory_dir(root)
    udir = uploads_dir(root)
    parts: list[str] = []
    pipe = mdir / "pipeline.json"
    if pipe.exists():
        try:
            parts.append(
                "### memory/pipeline.json\n```json\n"
                + pipe.read_text(encoding="utf-8")[:4000]
                + "\n```"
            )
        except OSError:
            pass
    for label, sdir in (("memory", mdir), ("uploads", udir)):
        if not sdir.exists():
            continue
        for p in sorted(sdir.glob("*.py"))[:20]:
            try:
                body = p.read_text(encoding="utf-8")
            except OSError:
                continue
            if len(body) > 2500:
                body = body[:2500] + "\n# …(截断)"
            parts.append(f"### {label}/{p.name}\n```python\n{body}\n```")
    text = "\n\n".join(parts) if parts else "（尚无 memory/ 脚本与管线）"
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…(脚本上下文截断)"
    return text
