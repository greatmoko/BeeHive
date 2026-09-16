"""文件定位、局部编辑与有界长文本暂存；所有提交经受保护的 BackendProtocol。"""

from __future__ import annotations

from typing import Any, Callable
from threading import RLock

_POSITIONS = ("before", "after", "replace")


FILESYSTEM_TOOL_DESCRIPTIONS = {
    "read_file": """读取文件，使用项目虚拟路径。小文件一次读取；已有上下文足够就直接编辑，勿反复读取。
大文件先 find_in_file/grep 定位，只读相关区域；不要从头到尾连续翻页。offset 从 0 起。
只在确有需要时继续读取 next_offset；到文件末尾即停止。""",
    "write_file": """创建或完整替换文件，使用项目虚拟路径。优先一次写入完整内容，无固定 3000 字限制。
局部修改用 edit_file/insert_text，勿重写整个网页。仅当完整内容超过模型单次输出预算时，
使用 write_file_chunk 暂存分块并在最后 final=True 提交，禁止把未完成的网页作为交付物。""",
    "edit_file": """对已有文件执行精确文本替换。已掌握当前原文可直接编辑，否则先 find_in_file/grep 定位。
old_string 使用唯一原文，保留缩进，不带行号。失败后重新定位一次；仍失败则报告错误，不盲目循环。
支持删除局部内容（new_string 为空）；删除整个文件使用 delete。""",
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


def build_file_tools(*, backend: Any, emit=None):
    """构造文件写入辅助工具（backend 为 runner 里的 CompositeBackend）。"""
    from langchain_core.tools import tool

    pending: dict[str, str] = {}
    pending_lock = RLock()

    @tool
    def read_file_range(file_path: str, offset: int = 0, limit: int = 500, char_offset: int = 0) -> str:
        """按行窗口读取文件片段（0 起始行号），用于修改前确认锚点与上下文。

        已有最新上下文时不必重复读取。需要确认时读取目标位置附近，
        锚点文本必须与文件实际内容（含缩进、全半角）完全一致。
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
        content = content[char_offset:char_offset + 10000]
        head = (
            f"[第 {start} 行起"
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
        同一文件必须顺序调用；中断会丢弃本轮暂存内容。局部编辑用 edit_file/insert_text。
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

    return [read_file_range, find_in_file, write_file_chunk, insert_text]
