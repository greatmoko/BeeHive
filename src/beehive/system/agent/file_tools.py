"""文件工具装配与稳定的模型说明入口。"""
from __future__ import annotations
from typing import Any
from beehive.system.agent.file_reads import build_read_tools
from beehive.system.agent.file_edits import build_edit_tools
from beehive.system.agent.file_chunks import build_chunk_tools
from beehive.system.agent.file_read_limits import wrap_read_file_soft_limit
from beehive.system.agent.file_content import _replace_line_range

READ_FILE_SOFT_LIMIT = 30_000
DESIGN_READ_FILE_SOFT_LIMIT = 50_000

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



def build_file_tools(*, backend: Any, emit=None):
    return (build_read_tools(backend=backend, emit=emit)
            + build_chunk_tools(backend=backend, emit=emit)
            + build_edit_tools(backend=backend, emit=emit))
