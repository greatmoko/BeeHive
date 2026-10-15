"""分页文件读取与纯文本定位。"""
from __future__ import annotations
from typing import Any
from beehive.system.agent.file_content import _read_all

def build_read_tools(*, backend: Any, emit=None):
    from langchain_core.tools import tool

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
        # 明确标记文件末尾，避免模型反复请求下一页。
        if next_off is None:
            ending = "；已到文件末尾，勿继续翻页"
        else:
            ending = f"；仅在需要时从 offset={next_off} 继续"
        char_offset = max(0, int(char_offset))
        if char_offset > len(content):
            return "错误：char_offset 超过该窗口字符数；不要继续翻页。"
        if len(content) - char_offset > 10000:
            ending = f"；窗口内文本未读完，next_char_offset={char_offset + 10000}（保持 offset/limit 不变）；优先搜索所需区域"
        # 长物理行分页继续使用原文件的绝对行号。
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

    return [read_file_range, find_in_file]
