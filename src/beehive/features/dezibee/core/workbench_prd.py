"""原型 PRD 定点改写与需求路径校验。"""
from __future__ import annotations
import json
import os
import re
from pathlib import Path
from beehive.features.dezibee.core.workbench_syntax import _match_bracket, _find_toplevel_key
class _BadRequest(Exception):
    """请求参数不合法（回 400，而不是 500）。"""

def _rewrite_prd_in_index(index_path: Path, prd_obj: dict) -> None:
    """把 index.html 中 WORKBENCH_DATA.prd 的值替换为 prd_obj（其余内容原样保留）。

    如此用户手改的 PRD 与 AI 编辑的是同一份真源（demo/index.html）。
    """
    text = index_path.read_text(encoding="utf-8")
    m = re.search(
        r"WORKBENCH_DATA\s*=\s*(?:window\.WORKBENCH_DATA\s*=\s*)?\{", text
    )
    if not m:
        raise _BadRequest("index.html 中未找到 WORKBENCH_DATA")
    obj_open = m.end() - 1
    obj_close = _match_bracket(text, obj_open)
    if obj_close is None:
        raise _BadRequest("WORKBENCH_DATA 结构不完整")
    found = _find_toplevel_key(text, obj_open, obj_close, "prd")
    if not found:
        raise _BadRequest("WORKBENCH_DATA 中未找到 prd")
    key_idx, _colon, val_idx = found
    if text[val_idx] != "{":
        raise _BadRequest("WORKBENCH_DATA.prd 不是对象")
    val_end = _match_bracket(text, val_idx)
    if val_end is None:
        raise _BadRequest("WORKBENCH_DATA.prd 结构不完整")

    # 按 prd 所在行的缩进对齐序列化结果，保持文件可读
    line_start = text.rfind("\n", 0, key_idx) + 1
    indent = text[line_start:key_idx]
    if indent.strip():
        indent = ""
    # `<` 转成 \u003c：JSON 语义不变，但避免内容里的 </script> 提前闭合外层 script 标签
    new_val = (
        json.dumps(prd_obj, ensure_ascii=False, indent=2)
        .replace("<", "\\u003c")
        .replace("\n", "\n" + indent)
    )
    new_text = text[:val_idx] + new_val + text[val_end + 1:]

    tmp = index_path.with_name(index_path.name + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    os.replace(tmp, index_path)

def _resolve_demo_index(root: Path, req_id) -> Path:
    """校验 req_id 并返回 <root>/<req_id>/demo/index.html（防目录穿越）。"""
    if not isinstance(req_id, str) or not req_id.strip():
        raise _BadRequest("缺少 req_id")
    if req_id.strip() != req_id or any(s in req_id for s in ("/", "\\", "\x00")) or ".." in req_id:
        raise _BadRequest("req_id 非法")
    root_res = Path(root).resolve()
    req_dir = (root_res / req_id).resolve()
    if req_dir != root_res and root_res not in req_dir.parents:
        raise _BadRequest("req_id 越界")
    index = req_dir / "demo" / "index.html"
    if not index.is_file():
        raise _BadRequest(f"未找到 {req_id}/demo/index.html")
    return index
