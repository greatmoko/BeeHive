"""文件工具的 Unicode 参数兼容与 grep 签名缓存。"""
from __future__ import annotations
import inspect
import re

_JSON_UNICODE_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")

def _decode_json_unicode_escapes(text: str) -> str:
    """只解一层 ``\\uXXXX``，供 edit_file 的双重转义兼容回退使用。"""
    return _JSON_UNICODE_ESCAPE_RE.sub(
        lambda match: chr(int(match.group(1), 16)), text
    )

# 按 backend 类型缓存 grep 签名，避免每次 grep 都 inspect.signature 内省。
_GREP_CONTEXT_CACHE: dict[type, bool] = {}


def _grep_accepts_context_lines(backend) -> bool:
    """内层 backend.grep 是否收 context_lines。

    deepagents 单个 FilesystemBackend/LocalShellBackend 收，但聚合的
    CompositeBackend.grep 不收。透传前用签名探测，避免把 context_lines
    传给 CompositeBackend 触发 `got an unexpected keyword argument`。
    签名由类决定，故按类型缓存探针结果。
    """
    cls = type(backend)
    if cls not in _GREP_CONTEXT_CACHE:
        try:
            _GREP_CONTEXT_CACHE[cls] = "context_lines" in inspect.signature(backend.grep).parameters
        except (TypeError, ValueError):
            _GREP_CONTEXT_CACHE[cls] = False
    return _GREP_CONTEXT_CACHE[cls]
