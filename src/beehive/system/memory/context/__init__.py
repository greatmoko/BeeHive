"""对话上下文管理 — token 估算、预算裁剪、摘要压缩（对齐 Chatbox 思路）。"""

from __future__ import annotations

from beehive.system.prompts import SUMMARY_SYSTEM_PROMPT

from dataclasses import dataclass
from datetime import datetime
from typing import Any

# Chatbox: reserve output headroom; threshold = available * compaction_threshold
OUTPUT_RESERVE_TOKENS = 4096
DEFAULT_COMPACTION_THRESHOLD = 0.6
TOKENS_PER_IMAGE = 1600
RETAIN_RECENT_RATIO = 0.4  # 压缩时保留最近约 40% 原文


from beehive.system.memory.context.usage import ContextUsage


from beehive.system.memory.context.tokens import estimate_text_tokens


from beehive.system.memory.context.tokens import estimate_content_tokens


from beehive.system.memory.context.tokens import estimate_message_tokens


from beehive.system.memory.context.tokens import estimate_messages_tokens


from beehive.system.memory.context.tokens import available_window


from beehive.system.memory.context.tokens import threshold_tokens


from beehive.system.memory.context.slicing import latest_compaction


from beehive.system.memory.context.slicing import slice_after_compaction


from beehive.system.memory.context.slicing import apply_message_count_limit


from beehive.system.memory.context.slicing import trim_to_token_budget


from beehive.system.memory.context.slicing import find_compaction_cut


from beehive.system.memory.context.summary import mechanical_summary


from beehive.system.memory.context.summary import build_summary_prompt_messages


from beehive.system.memory.context.summary import append_compaction_point


from beehive.system.memory.context.usage import estimate_session_usage


from beehive.system.memory.context.usage import needs_compaction


from beehive.system.memory.context.usage import plan_compaction


from beehive.system.memory.context.usage import build_context_message_dicts


from beehive.system.memory.context.summary import summary_as_message
