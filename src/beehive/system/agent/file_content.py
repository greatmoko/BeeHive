"""文件原文读写与按行替换，不绕过受保护后端。"""
from __future__ import annotations
from typing import Any, Callable

def _notify(emit: Callable[[str, str, dict], None] | None, content: str) -> None:
    if emit:
        try:
            emit("info", content, {})
        except Exception:
            pass

def _read_all(backend: Any, file_path: str) -> str | None:
    """读整个文件；文件不存在、读取失败或抛异常返回 None（由调用方区分提示）。"""
    try:
        res = backend.read(file_path, offset=0, limit=100_000_000)
    except Exception:
        return None
    if res is None or getattr(res, "error", None):
        return None
    data = getattr(res, "file_data", None) or {}
    if data.get("encoding") == "base64" or getattr(res, "next_offset", None) is not None:
        return None
    from deepagents.backends.utils import EMPTY_CONTENT_WARNING
    content = str(data.get("content") or "")
    return "" if content == EMPTY_CONTENT_WARNING else content

def _write_all(backend: Any, file_path: str, content: str) -> str | None:
    """写整个文件；成功返回 None，失败返回错误文本（backend 错误或异常兜底）。"""
    try:
        res = backend.write(file_path, content)
    except Exception as e:
        return str(e)
    err = getattr(res, "error", None)
    return str(err) if err else None

def _replace_line_range(
    existing: str, start_line: int, end_line: int, new_string: str
) -> str:
    """按 1-based 闭区间替换行；允许在 EOF 的下一行插入。"""
    start = int(start_line)
    end = int(end_line)
    if start < 1 or end < start:
        raise ValueError("start_line/end_line 必须是有效的 1-based 闭区间")
    lines = existing.splitlines(keepends=True)
    count = len(lines)
    if start > count + 1 or end > count + 1:
        raise ValueError(f"行号越界：文件共 {count} 行")
    if start == count + 1 and end != start:
        raise ValueError("文件末尾插入时 start_line 与 end_line 必须相同")

    left = start - 1
    right = end if start <= count else count
    replacement = str(new_string or "")
    newline = "\r\n" if "\r\n" in existing else "\n"
    if replacement and right < count and not replacement.endswith(("\n", "\r")):
        replacement += newline
    return "".join(lines[:left]) + replacement + "".join(lines[right:])
