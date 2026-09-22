"""DeepSeek 前缀缓存友好的公共模块（按职责拆分为 4 个子模块）。

原 cache_prefix.py 混合了提示词构建/续跑启发式/工具内省包装/缓存统计/PrefixGuard 等
职责，现拆分至：
- sysprompt.py：集中维护各模块 system；prompt.py：动态【会话上下文】块
- intent_heuristics.py：AI 回复续跑启发式
- tool_truncate.py：工具运行控制/名称/排序稳定化
- cache_stats.py：缓存命中观测 + 前缀护栏

本文件保留为兼容 re-export（既有 `from wokbee.engine.cache_prefix import ...` 不变）。
"""

from __future__ import annotations

from wokbee.engine.prompt import build_session_context_block, compose_user_with_context
from sysprompt import static_system_prompt
from wokbee.engine.intent_heuristics import ai_reply_suggests_pending_action
from wokbee.engine.tool_truncate import (
    prefix_fingerprint,
    sort_tools_by_name,
    tool_name_of,
    wrap_tools_runtime_controls,
)
from wokbee.engine.cache_stats import (
    CacheHitTracker,
    PrefixGuard,
    extract_cache_tokens,
)

__all__ = [
    "CacheHitTracker",
    "PrefixGuard",
    "ai_reply_suggests_pending_action",
    "build_session_context_block",
    "compose_user_with_context",
    "extract_cache_tokens",
    "prefix_fingerprint",
    "sort_tools_by_name",
    "static_system_prompt",
    "tool_name_of",
    "wrap_tools_runtime_controls",
]
