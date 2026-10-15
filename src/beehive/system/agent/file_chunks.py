"""单次工具集合独占分块暂存、位置校验和完整提交。"""
from __future__ import annotations
from typing import Any
from threading import RLock
from beehive.system.agent.file_content import _notify, _write_all

def build_chunk_tools(*, backend: Any, emit=None):
    from langchain_core.tools import tool
    pending: dict[str, str] = {}
    pending_lock = RLock()

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
            # 限制整轮暂存总量，包含未提交的文件。
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

    return [write_file_chunk]
