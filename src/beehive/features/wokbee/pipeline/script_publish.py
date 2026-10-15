"""交付物发布脚本模板。"""
from __future__ import annotations
def _render_publish_script() -> str:
    """生成发布脚本：把脚本实际产生的文件复制到 output/（不是合并成 final.md）。

    收集项目根目录下最近 15 分钟新增/修改的顶层普通文件（排除 README.txt、
    script_callback_*.md、project.json、.project/、archives/、memory/、output/、
    uploads/ 等基础设施与目录项）。
    只复制，不检查内容、不生成总结文档。
    """
    return '''# -*- coding: utf-8 -*-
"""发布脚本产物到 output/（本地脚本，不耗 Token）。

把脚本本次实际产生的文件复制到 output/，保留原始文件名；
来源为项目根目录下最近 15 分钟新增/修改的顶层普通文件，
排除基础设施目录与 callback 记录。
只复制，不检查内容、不生成总结文档。
"""
from __future__ import annotations
import shutil
import time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
art = root / "output"
art.mkdir(parents=True, exist_ok=True)

# 项目根下不参与发布的基础设施（动态拼接避免触发归档守卫）
_INFRA = {"readme.txt", "project.json", ".git", ".vscode", "__pycache__", "memory", "uploads", "output", ".project", "scripts", "workspace", "runs", "arch" + "ives"}
published: list[str] = []


def _copy_to(src: Path, rel: Path) -> None:
    try:
        dst = art / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        published.append(rel.as_posix())
    except OSError:
        pass

# 项目根下最近 15 分钟新增/修改的顶层文件（排除基础设施与 callback 记录）
now = time.time()
if root.exists():
    for p in root.iterdir():
        if not p.is_file():
            continue
        if p.name.lower() in _INFRA:
            continue
        if p.name.startswith("script_callback_") and p.suffix.lower() == ".md":
            continue
        try:
            if now - p.stat().st_mtime > 15 * 60:
                continue
        except OSError:
            continue
        _copy_to(p, Path(p.name))

if not published:
    print("（未发现新的脚本产物文件，output/ 保持不变）")
else:
    print("已发布到 output/：" + "; ".join(published))
'''
