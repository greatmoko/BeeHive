"""内置全文读取软上限与同步/异步工具适配。"""
from __future__ import annotations
import inspect
import re
from functools import wraps
from typing import Any

def _soft_limit_read_result(result: Any, *, max_chars: int, kwargs: dict) -> Any:
    """为全文读取保留明确分页提示，不改变范围读取。"""
    try:
        offset = int(kwargs.get("offset", 0) or 0)
        limit = int(kwargs.get("limit", 100) or 100)
    except (TypeError, ValueError):
        return result
    # 只限制从首行开始的明确全文读取，保留小窗口读取。
    if offset != 0 or limit <= 2000:
        return result
    content = getattr(result, "content", None)
    if not isinstance(content, str) or len(content) <= max_chars:
        return result

    prefix = content[:max_chars]
    cut = prefix.rfind("\n")
    if cut > 0:
        prefix = prefix[:cut]
    rows = list(
        re.finditer(
            r"^(?:L\s*)?(\d+)(?:\.\d+)?(?:\s*\||\s{2})",
            prefix,
            re.MULTILINE,
        )
    )
    shown = int(rows[-1].group(1)) if rows else prefix.count("\n")
    total_match = re.search(r"of (\d+) total", content)
    total = int(total_match.group(1)) if total_match else max(shown, content.count("\n"))
    notice = (
        f"\n\n文件共 {total} 行，已显示前 {shown} 行；"
        f"继续读取请用 read_file_range(offset={shown})。"
    )
    clipped = prefix + notice
    try:
        return result.model_copy(update={"content": clipped})
    except AttributeError:
        try:
            result.content = clipped
        except Exception:
            return result
        return result

def wrap_read_file_soft_limit(middleware: Any, *, max_chars: int) -> None:
    """只为内置 read_file 工具增加字符软上限。"""
    for index, tool in enumerate(getattr(middleware, "tools", []) or []):
        if getattr(tool, "name", "") != "read_file":
            continue

        def wrap(fn):
            @wraps(fn)
            def wrapped(*args, **kwargs):
                call_kwargs = dict(kwargs)
                if "offset" not in call_kwargs and len(args) > 2:
                    call_kwargs["offset"] = args[2]
                if "limit" not in call_kwargs and len(args) > 3:
                    call_kwargs["limit"] = args[3]
                return _soft_limit_read_result(
                    fn(*args, **kwargs), max_chars=max_chars, kwargs=call_kwargs
                )

            wrapped.__name__ = getattr(fn, "__name__", "read_file")
            return wrapped

        original_func = getattr(tool, "func", None)
        if not callable(original_func):
            return
        updates = {"func": wrap(original_func)}
        coroutine = getattr(tool, "coroutine", None)
        if inspect.iscoroutinefunction(coroutine):
            @wraps(coroutine)
            async def wrapped_async(*args, **kwargs):
                call_kwargs = dict(kwargs)
                if "offset" not in call_kwargs and len(args) > 2:
                    call_kwargs["offset"] = args[2]
                if "limit" not in call_kwargs and len(args) > 3:
                    call_kwargs["limit"] = args[3]
                result = await coroutine(*args, **kwargs)
                return _soft_limit_read_result(
                    result, max_chars=max_chars, kwargs=call_kwargs
                )

            updates["coroutine"] = wrapped_async
        try:
            middleware.tools[index] = tool.model_copy(update=updates)
        except Exception:
            for name, value in updates.items():
                try:
                    object.__setattr__(tool, name, value)
                except Exception:
                    pass
        return
