"""锚点与行范围局部编辑，保留快照比较及并发保护。"""
from __future__ import annotations
from typing import Any
from beehive.system.agent.file_content import _notify, _read_all, _replace_line_range

_POSITIONS = ("before", "after", "replace")

def build_edit_tools(*, backend: Any, emit=None):
    from langchain_core.tools import tool

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
        # 在后端编辑锁内比较原文快照，避免覆盖其他并发修改。
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

    return [insert_text, edit_file_lines]
