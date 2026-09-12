"""文件写入辅助工具：分块写入与锚点插入，绕开单次工具调用被模型截断的限制。

write_file 的 content 参数是模型单次生成的 JSON，受模型 max_tokens 约束——长文一次传入
会被截断成 invalid_tool_call（deepagents PatchToolCallsMiddleware 注入
"arguments were malformed or truncated"，写入不执行）。本模块提供：

- write_file_chunk：长文分块（overwrite 写首块 → append 追加后续块）
- insert_text：按唯一文本锚点在指定位置插入/替换
- read_file_range：按行窗口读取（改前确认锚点）

三个工具都经由 deepagents BackendProtocol 读写，自动继承 WokBee 的全部路径守卫
（archives/ 禁止、/skills/ 只读、未授权真实路径拦截、虚拟路径校验）。
"""

from __future__ import annotations

from typing import Any, Callable

_POSITIONS = ("before", "after", "replace")


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
    return str(data.get("content") or "")


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

    @tool
    def read_file_range(file_path: str, offset: int = 0, limit: int = 100) -> str:
        """按行窗口读取文件片段（0 起始行号），用于修改前确认锚点与上下文。

        在调用 insert_text / edit_file 之前，先读取目标位置附近若干行，
        确认锚点文本唯一且与文件实际内容（含缩进、全半角）完全一致。
        limit=0 或不传 limit 的 read_file 读全文件（大文件慎用）。
        """
        res = backend.read(file_path, offset=max(0, int(offset)), limit=max(1, int(limit)))
        err = getattr(res, "error", None)
        if err:
            return f"错误：读取失败——{err}"
        data = getattr(res, "file_data", None) or {}
        content = str(data.get("content") or "")
        start = getattr(res, "start_line", None)
        next_off = getattr(res, "next_offset", None)
        total = getattr(res, "total_lines", None)
        head = (
            f"[第 {start} 行起"
            + (f"，共 {total} 行" if total is not None else "")
            + (f"；后续从 offset={next_off} 继续" if next_off is not None else "")
            + "]"
        )
        return head + "\n" + (content or "（窗口内无内容）")

    @tool
    def write_file_chunk(
        file_path: str,
        content: str,
        mode: str = "append",
    ) -> str:
        """分块写入文件——长文档（超过约 3000 字）必须用本工具分多次写入，不要用 write_file 一次传全文。

        工作流：第一次用 mode="overwrite" 写文档开头；之后每块用 mode="append" 追加到文末。
        每块 content 控制在 3000~4000 字以内，块间注意自己接好换行（append 直接拼在原文之后）。
        最后一块写完后可用 read_file_range 抽查文末确认完整。
        mode 仅支持 "overwrite"（清空重写）与 "append"（追加到文末）。
        """
        if mode not in ("overwrite", "append"):
            return f"错误：mode 只支持 overwrite/append，收到 {mode!r}"
        if not content:
            return "错误：content 不能为空"
        if mode == "overwrite":
            err = _write_all(backend, file_path, content)
            if err:
                return f"错误：写入失败——{err}\n路径提示：文件工具只能用虚拟路径（workspace/、deliverables/ 等）；archives/ 与 /skills/ 不可写。"
            _notify(emit, f"已写入文件首块：{file_path}（{len(content)} 字）")
            return f"已写入 {file_path} 首块（{len(content)} 字）。继续用 mode=\"append\" 追加后续块。"
        existing = _read_all(backend, file_path)
        if existing is None:
            return (
                f"错误：无法读取 {file_path}（可能不存在）。"
                "首次写入请用 mode=\"overwrite\"，或先用 read_file 确认文件存在。"
            )
        merged = existing + content
        err = _write_all(backend, file_path, merged)
        if err:
            return f"错误：追加失败——{err}"
        _notify(emit, f"已追加文件块：{file_path}（本次 {len(content)} 字，全文 {len(merged)} 字）")
        return f"已追加到 {file_path}（本次 {len(content)} 字，全文共 {len(merged)} 字）。"

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
        长内容（>3000 字）请改用 write_file_chunk 分块写入，本工具适合定点插入/替换短段。
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
                idx = hay.find(needle, idx + 1)
            end = idx + len(needle)
            if position == "before":
                final = hay[:idx] + text + hay[idx:]
            elif position == "after":
                final = hay[:end] + text + hay[end:]
            else:  # replace
                final = hay[:idx] + text + hay[end:]
        if final == hay:
            return "提示：内容无变化（插入文本与锚点相同？）。未写入。"
        err = _write_all(backend, file_path, final)
        if err:
            return f"错误：写入失败——{err}"
        action = {"before": "前插入", "after": "后插入", "replace": "替换"}[position]
        _notify(emit, f"已在 {file_path} 锚点{action}文本（{len(text)} 字）")
        return f"已在 {file_path} 完成锚点{action}（{len(text)} 字，全文 {len(final)} 字）。"

    return [read_file_range, write_file_chunk, insert_text]
