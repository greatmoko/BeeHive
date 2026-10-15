"""工具名称、稳定排序和提示词前缀指纹。"""
from __future__ import annotations
import hashlib
from typing import Any

def tool_name_of(tool: Any) -> str:
    name = getattr(tool, "name", None)
    if name:
        return str(name)
    return str(tool)

def sort_tools_by_name(tools: list) -> list:
    """稳定排序，避免 MCP 返回顺序抖动破坏 tools 前缀。

    顶层按小写名排序；同名（含大小写不一致）再用原名作二级键兜底，
    保证不同轮次即便输入顺序不同，也得到完全一致的排序。
    """
    return sorted(
        list(tools or []),
        key=lambda t: (tool_name_of(t).lower(), tool_name_of(t)),
    )

def prefix_fingerprint(system_prompt: str, tool_names: list[str]) -> str:
    h = hashlib.sha256()
    h.update((system_prompt or "").encode("utf-8"))
    h.update(b"\0")
    h.update("\n".join(tool_names).encode("utf-8"))
    return h.hexdigest()[:12]
