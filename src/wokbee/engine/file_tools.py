"""文件定位、局部编辑与有界长文本暂存；所有提交经受保护的 BackendProtocol。"""

from __future__ import annotations

import inspect
import re
from typing import Any, Callable
from threading import RLock

_POSITIONS = ("before", "after", "replace")
READ_FILE_SOFT_LIMIT = 30_000
DESIGN_READ_FILE_SOFT_LIMIT = 50_000


def _soft_limit_read_result(result: Any, *, max_chars: int, kwargs: dict) -> Any:
    """Keep full read pagination explicit without changing range reads."""
    try:
        offset = int(kwargs.get("offset", 0) or 0)
        limit = int(kwargs.get("limit", 100) or 100)
    except (TypeError, ValueError):
        return result
    # The built-in tool uses small limits for intentional windows; only cap an
    # offset-zero request that is explicitly large enough to be a full read.
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
    """Add a soft character cap to the built-in read_file tool only."""
    for index, tool in enumerate(getattr(middleware, "tools", []) or []):
        if getattr(tool, "name", "") != "read_file":
            continue

        def wrap(fn):
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


FILESYSTEM_TOOL_DESCRIPTIONS = {
    "read_file": """读取文件，使用项目虚拟路径。可按需全文读取；大文件分窗口读取并继续到目标上下文完整。
修改前必须读取目标文件的最新内容；PRD 修改尤其必须先读取当前目标章节，不得使用旧对话文本覆盖用户手改。
读取结果带 L042 形式的行号，行号只用于定位，不要复制进 anchor、old_string 或写回内容。""",
    "write_file": """创建或完整替换新文件，使用项目虚拟路径。已有 demo/index.html 或 PRD 的局部修改不得使用此工具。
	局部修改优先用 edit_file_lines；需要精确文本替换时用 edit_file/insert_text。PRD-only 请求禁止重写整个入口文件。仅当用户明确要求全文重写，或创建新文件时才完整写入。
	工作台校验发现 WORKBENCH_DATA 不完整时，文件仍会先写入，工具会返回非阻断告警；继续读取当前文件并补全，不要把它当作写入失败。""",
    "edit_file": """对已有文件执行精确文本替换。修改前必须先读取当前原文；已掌握当前原文可直接编辑，否则先 find_in_file/grep 定位。
	old_string 使用唯一原文，保留缩进，不带行号。失败后重新定位一次；仍失败则报告错误，不盲目循环。
	支持删除局部内容（new_string 为空）；删除整个文件使用 delete。
	整段连续行的小范围编辑优先使用 edit_file_lines；本工具保留给按精确文本替换的场景。""",
    "edit_file_lines": """按行号替换已有文件中的一段连续内容。start_line/end_line 使用 1-based 且包含首尾行；先用 read_file_range 或 find_in_file 确认行号。
这是整段小范围编辑的首选工具：只接受 file_path、start_line、end_line、new_string，不需要也不接受 old_string。new_string 为空表示删除该行范围；start_line=end_line=文件末行+1 可在文件末尾插入。
如果请求包含 old_string，请改用 edit_file；如果缺少 start_line/end_line，请先调用 read_file_range 或 find_in_file。
只替换指定行，其他内容保持不变；写入后若工作台校验发现 WORKBENCH_DATA 不完整，文件仍已写入，请继续读取并修复。""",
}


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


def build_file_tools(*, backend: Any, emit=None):
    """构造文件写入辅助工具（backend 为 runner 里的 CompositeBackend）。"""
    from langchain_core.tools import tool

    # 每次构建工具独立持有暂存和锁；不同 backend/需求的同名虚拟路径不会共享内容。
    pending: dict[str, str] = {}
    pending_lock = RLock()

    @tool
    def read_file_range(file_path: str, offset: int = 0, limit: int = 500, char_offset: int = 0) -> str:
        """按行窗口读取文件片段（0 起始 offset），用于修改前确认最新锚点与上下文。

        可连续分页全文读取；PRD 修改前必须先读取当前目标章节。返回内容的每行带绝对行号（如 L042 |），
        行号只供定位，不能复制到 anchor、old_string 或写回内容。锚点文本必须与文件实际内容
        （含缩进、全半角）完全一致。
        超长单行可保持 offset/limit 不变，按返回的 next_char_offset 设置 char_offset。
        未知位置时先用 find_in_file 获取行号；只有文件工具已报错且重新定位仍失败时，才可用临时脚本兜底。
        """
        try:
            res = backend.read(file_path, offset=max(0, int(offset)), limit=max(1, min(2000, int(limit))))
        except Exception as exc:
            return f"错误：读取失败——{exc}"
        err = getattr(res, "error", None)
        if err:
            return f"错误：读取失败——{err}"
        data = getattr(res, "file_data", None) or {}
        content = str(data.get("content") or "")
        start = getattr(res, "start_line", None)
        start_line = int(start or 1)
        next_off = getattr(res, "next_offset", None)
        total = getattr(res, "total_lines", None)
        # Explicit EOF prevents the model from treating every result as another page.
        if next_off is None:
            ending = "；已到文件末尾，勿继续翻页"
        else:
            ending = f"；仅在需要时从 offset={next_off} 继续"
        char_offset = max(0, int(char_offset))
        if char_offset > len(content):
            return "错误：char_offset 超过该窗口字符数；不要继续翻页。"
        if len(content) - char_offset > 10000:
            ending = f"；窗口内文本未读完，next_char_offset={char_offset + 10000}（保持 offset/limit 不变）；优先搜索所需区域"
        # char_offset may continue a very long physical line. Keep the absolute
        # source line number on that continuation instead of resetting to the window start.
        display_start = start_line + content[:char_offset].count("\n")
        content = content[char_offset:char_offset + 10000]
        numbered: list[str] = []
        for index, line in enumerate(content.splitlines(keepends=True)):
            numbered.append(f"L{display_start + index:03d} | {line}")
        if content and not numbered:
            numbered.append(f"L{display_start:03d} | {content}")
        content = "".join(numbered)
        head = (
            f"[第 {display_start} 行起"
            + (f"，共 {total} 行" if total is not None else "")
            + ending
            + "]"
        )
        return head + "\n" + (content or "（窗口内无内容）")

    @tool
    def find_in_file(
        file_path: str,
        query: str,
        context_chars: int = 300,
        max_matches: int = 3,
    ) -> str:
        """在当前文件中按字面纯文本查找，返回命中次数、行号和附近原文。

        用于编辑前定位锚点：query 取稳定的短文本，例如 `"id": "sec-filter-intro"`、
        某个函数名或独特标题。不是正则表达式。返回的原文可直接作为后续
        read_file_range 的行号依据或 insert_text 的锚点；不需要创建/运行 Python 脚本。
        """
        if not query or len(query) > 1000:
            return "错误：query 必须是 1~1000 字的短文本"
        existing = _read_all(backend, file_path)
        if existing is None:
            return f"错误：无法读取 {file_path}（可能不存在）。"
        context = max(80, min(1000, int(context_chars)))
        cap = max(1, min(5, int(max_matches)))
        starts: list[int] = []
        start = 0
        while len(starts) < cap + 1:
            found = existing.find(query, start)
            if found < 0:
                break
            starts.append(found)
            start = found + len(query)
        if not starts:
            return f"未在 {file_path} 找到 {query!r}。请换用更短或更准确的纯文本查询。"
        more = len(starts) > cap
        starts = starts[:cap]
        blocks = [
            f"在 {file_path} 找到{'至少 ' if more else ''}{len(starts)} 处匹配；"
            "以下是当前文件原文，勿包含行号作为编辑锚点："
        ]
        for number, index in enumerate(starts, 1):
            line = existing.count("\n", 0, index) + 1
            left = max(0, index - context)
            right = min(len(existing), index + len(query) + context)
            blocks.append(f"\n[匹配 {number}，第 {line} 行附近]\n{existing[left:right]}")
        if more:
            blocks.append("\n匹配超过显示上限；请用更长的 query 使锚点唯一。")
        return "\n".join(blocks)

    @tool
    def write_file_chunk(
        file_path: str,
        content: str,
        mode: str = "append",
        offset: int = 0,
        final: bool = False,
    ) -> str:
        """仅在超过模型单次输出预算时暂存长文件，最后一次性提交完整内容。

        首块 mode=overwrite, offset=0；后续 mode=append, offset=上次返回的 next_offset（字符数）。
        最后一块必须 final=True；也可用空 content、final=True 提交。未提交时目标文件不变。
        同一文件必须顺序调用；中断会丢弃本轮暂存内容。局部编辑优先用 edit_file_lines；需要精确文本替换时用 edit_file/insert_text。
        """
        if mode not in ("overwrite", "append") or offset < 0:
            return "错误：mode 仅支持 overwrite/append，offset 必须非负。"
        key = file_path.replace("\\", "/").lstrip("/")
        with pending_lock:
            if mode == "overwrite":
                if offset != 0:
                    return "错误：首块 offset 必须为 0。"
                existing = ""
            else:
                if key not in pending:
                    return "错误：没有待提交内容。请从 mode=overwrite 开始；原文件未修改。"
                existing = pending[key]
                if offset != len(existing):
                    return f"错误：分块位置不匹配，next_offset={len(existing)}；未追加，避免重复或乱序。"
            merged = existing + content
            # Bound total per-run staging memory, including abandoned files.
            if sum(len(v) for k, v in pending.items() if k != key) + len(merged) > 8_000_000:
                return "错误：本轮暂存超过 800 万字符。请拆成独立文件；原文件未修改。"
            pending[key] = merged
            if not final:
                return f"已暂存 {file_path}；next_offset={len(merged)}。目标文件未修改；最后必须 final=True 提交。"
            err = _write_all(backend, file_path, merged)
            if err:
                return f"错误：提交失败——{err}；暂存仍保留，next_offset={len(merged)}。"
            del pending[key]
            _notify(emit, f"已提交完整文件：{file_path}（{len(merged)} 字）")
            return f"已提交 {file_path}（{len(merged)} 字），分块写入已结束。"

    @tool
    def insert_text(
        file_path: str,
        anchor: str,
        text: str,
        position: str = "after",
        occurrence: int = 1,
        replace_all: bool = False,
    ) -> str:
        """在文件中按锚点文本定位，把 text 插入到锚点之前/之后，或替换锚点本身。

        用法与要求：
        - anchor：目标位置附近的**唯一**文本片段（建议 1-3 行，与文件内容逐字一致，含缩进）；
          不要整段复制长文。插入前先用 read_file_range 读取目标区域确认锚点。
        - position：before（插到锚点前）/ after（插到锚点后）/ replace（用 text 替换锚点）。
        - occurrence：锚点第几次出现（默认 1）；replace_all=True 时替换全部出现（仅 position=replace 有效）。
        - 插入的内容注意自带的换行（锚点前后是否需要补 \\n 由你保证）。
        本工具用于局部插入/替换；不要为了新增一段内容分块重写整个文件。
        """
        if position not in _POSITIONS:
            return f"错误：position 只支持 before/after/replace，收到 {position!r}"
        if not anchor:
            return "错误：anchor 不能为空"
        if position != "replace" and replace_all:
            return "错误：replace_all 仅在 position=\"replace\" 时有效"
        existing = _read_all(backend, file_path)
        if existing is None:
            return f"错误：无法读取 {file_path}（可能不存在），先用 read_file 确认。"
        hay = existing.replace("\r\n", "\n")
        needle = anchor.replace("\r\n", "\n")
        n = hay.count(needle)
        if n == 0:
            return (
                f"错误：在 {file_path} 中找不到锚点。请先用 read_file_range 读取目标位置附近内容，"
                "确认锚点与文件逐字一致（含缩进、全半角、标点）；锚点建议取 1-3 行唯一片段。"
            )
        occ = max(1, int(occurrence))
        if n > 1 and not replace_all and occ == 1:
            return (
                f"错误：锚点在文件中出现了 {n} 次，不唯一。请取更长、更独特的片段，"
                f"或用 occurrence 指定第几次出现（1~{n}）。"
            )
        if replace_all:
            # replace_all 仅 position=replace 有效（前面已校验）
            final = hay.replace(needle, text)
        else:
            if occ > n:
                return f"错误：occurrence={occ} 超出实际出现次数 {n}。"
            idx = -1
            for _ in range(occ):
                idx = hay.find(needle, 0 if idx < 0 else idx + len(needle))
            end = idx + len(needle)
            if position == "before":
                final = hay[:idx] + text + hay[idx:]
            elif position == "after":
                final = hay[:end] + text + hay[end:]
            else:  # replace
                final = hay[:idx] + text + hay[end:]
        if final == hay:
            return "提示：内容无变化（插入文本与锚点相同？）。未写入。"
        # Compare the snapshot inside the backend's edit lock: do not overwrite a
        # concurrent edit with the stale whole-file content read above.
        try:
            result = backend.edit(file_path, existing, final, replace_all=False)
            err = getattr(result, "error", None)
        except Exception as exc:
            err = str(exc)
        if err:
            return f"错误：写入失败——{err}"
        action = {"before": "前插入", "after": "后插入", "replace": "替换"}[position]
        _notify(emit, f"已在 {file_path} 锚点{action}文本（{len(text)} 字）")
        return f"已在 {file_path} 完成锚点{action}（{len(text)} 字，全文 {len(final)} 字）。"

    @tool
    def edit_file_lines(
        file_path: str,
        start_line: int,
        end_line: int,
        new_string: str,
    ) -> str:
        """按 1-based 闭区间替换连续行；整段小范围编辑优先使用本工具。

        本工具只接受 file_path、start_line、end_line、new_string。
        缺少行号时先调用 read_file_range 或 find_in_file；需要 old_string 时改用 edit_file。
        """
        existing = _read_all(backend, file_path)
        if existing is None:
            return f"错误：无法读取 {file_path}（可能不存在），先用 read_file_range 确认。"
        try:
            final = _replace_line_range(existing, start_line, end_line, new_string)
        except (TypeError, ValueError) as exc:
            return f"错误：无法按行编辑 {file_path}——{exc}"
        if final == existing:
            return "提示：内容无变化，未写入。"
        try:
            result = backend.edit(file_path, existing, final, replace_all=False)
            err = getattr(result, "error", None)
        except Exception as exc:
            err = str(exc)
            result = None
        if err:
            return f"错误：写入失败——{err}"
        detail = getattr(result, "path", None) if result is not None else None
        warning = ""
        if detail and str(detail) != file_path:
            warning = f"；{detail}"
        return (
            f"已按行编辑 {file_path}（第 {start_line}-{end_line} 行，"
            f"写入 {len(new_string or '')} 字，全文 {len(final)} 字）{warning}"
        )

    return [read_file_range, find_in_file, write_file_chunk, insert_text, edit_file_lines]
