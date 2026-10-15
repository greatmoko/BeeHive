"""脚本文件命名与文件名校验。"""
from __future__ import annotations
import re
from datetime import datetime
from pathlib import Path
AI_SCRIPT_EXTENSIONS = frozenset(
    {".py", ".bat", ".cmd", ".ps1", ".json", ".sh", ".js", ".vbs"}
)

_RESERVED_SCRIPT_NAMES = frozenset({"pipeline.json"})

def make_script_name(project_id: str, purpose: str, ext: str = ".py", ts: str | None = None) -> str:
    """规范脚本命名：项目ID_脚本作用(≤4个词)_时间戳(年月日时分秒)。

    例：`proj_abc_运行用户脚本_20260909160512.py`。
    """
    from datetime import datetime

    ext = (ext or ".py").lower()
    if not ext.startswith("."):
        ext = f".{ext}"
    pid = re.sub(r"[^\w\-]+", "_", (project_id or "project").strip()).strip("_")[:20] or "project"
    raw = re.sub(r"[^\w\u4e00-\u9fff\-]+", "_", (purpose or "script").strip())
    # 分词：英文按 _/-/空格；中文按 2 字一组近似“词”；取前 4 词
    tokens = [t for t in re.split(r"[\s_\-]+", raw) if t]
    words: list[str] = []
    for t in tokens:
        if re.fullmatch(r"[\u4e00-\u9fff]+", t):
            words.extend(t[i : i + 2] for i in range(0, len(t), 2))
        else:
            words.append(t)
        if len(words) >= 4:
            break
    purpose_ok = "_".join(words[:4])[:30].strip("_") or "script"
    ts = ts or datetime.now().strftime("%Y%m%d%H%M%S")
    return f"{pid}_{purpose_ok}_{ts}{ext}"

def sanitize_ai_script_filename(name: str) -> str | None:
    """只保留 memory/ 下安全文件名；拒绝路径穿越与保留名。"""
    raw = (name or "").strip().replace("\\", "/")
    if not raw or ".." in raw.split("/"):
        return None
    base = Path(raw).name
    if not base or base.lower() in _RESERVED_SCRIPT_NAMES:
        return None
    # 去掉奇怪前缀
    base = re.sub(r"[^\w.\-一-龥]+", "_", base).strip("._")
    if not base:
        return None
    ext = Path(base).suffix.lower()
    if ext not in AI_SCRIPT_EXTENSIONS:
        # 无扩展名时默认 .py；未知扩展名拒绝（避免写入任意二进制伪装）
        if not ext:
            base = f"{base}.py"
            ext = ".py"
        else:
            return None
    stem = Path(base).stem[:80] or "script"
    stem = re.sub(r"[^\w.\-一-龥]+", "_", stem).strip("._") or "script"
    return f"{stem}{ext}"
