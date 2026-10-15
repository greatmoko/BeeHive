"""按职责组织的 usage。"""
from __future__ import annotations
from dataclasses import dataclass
from beehive.system.memory.context.slicing import apply_message_count_limit
from beehive.system.memory.context.tokens import available_window
from beehive.system.memory.context.tokens import estimate_messages_tokens
from beehive.system.memory.context.tokens import estimate_text_tokens
from beehive.system.memory.context.slicing import find_compaction_cut
from beehive.system.memory.context.slicing import latest_compaction
from beehive.system.memory.context.slicing import slice_after_compaction
from beehive.system.memory.context.tokens import threshold_tokens
from beehive.system.memory.context.slicing import trim_to_token_budget
DEFAULT_COMPACTION_THRESHOLD = 0.6
TOKENS_PER_IMAGE = 1600


@dataclass
class ContextUsage:
    used: int
    limit: int
    threshold: int
    message_count: int
    max_message_count: int
    ratio: float  # used / limit，limit<=0 时为 0

    @property
    def percent(self) -> float:
        return self.ratio * 100.0


def estimate_session_usage(
    *,
    messages: list[dict],
    compaction_points: list[dict] | None,
    system_prompt: str,
    max_context_message_count: int,
    context_window: int,
    compaction_threshold: float = DEFAULT_COMPACTION_THRESHOLD,
    max_output: int | None = None,
    draft_text: str = "",
    pending_image_count: int = 0,
) -> ContextUsage:
    summary, active, _ = slice_after_compaction(messages, compaction_points)
    active = apply_message_count_limit(active, max_context_message_count)
    used = estimate_text_tokens(system_prompt.strip())
    if summary:
        used += estimate_text_tokens(summary) + 8
    used += estimate_messages_tokens(active)
    if draft_text.strip():
        used += estimate_text_tokens(draft_text)
    used += pending_image_count * TOKENS_PER_IMAGE
    limit = context_window if context_window > 0 else 0
    thr = threshold_tokens(context_window, compaction_threshold, max_output) if limit else 0
    ratio = (used / limit) if limit > 0 else 0.0
    return ContextUsage(
        used=used,
        limit=limit,
        threshold=thr,
        message_count=len(active),
        max_message_count=max_context_message_count,
        ratio=min(ratio, 9.99),
    )


def needs_compaction(
    usage: ContextUsage,
    *,
    auto_compaction: bool = True,
    min_messages: int = 4,
) -> bool:
    if not auto_compaction:
        return False
    if usage.limit <= 0 or usage.threshold <= 0:
        return False
    if usage.message_count < min_messages:
        return False
    return usage.used > usage.threshold


def plan_compaction(
    messages: list[dict],
    points: list[dict] | None,
) -> tuple[list[dict], list[dict], int, str, int] | None:
    """
    规划一次压缩。
    返回 (to_compact, retained, new_boundary_index, previous_summary, pin_end)；无法压缩时 None。

    pin_end：全量 messages 中钉住前缀的结束下标（不含），通常为首条 user 之后。
    """
    summary, active, boundary = slice_after_compaction(messages, points)
    prev = latest_compaction(points)
    pin_end = 0
    if prev:
        try:
            pin_end = int(prev.get("pin_end", 0) or 0)
        except (TypeError, ValueError):
            pin_end = 0
    if pin_end <= 0:
        for i, m in enumerate(messages or []):
            if (m.get("role") or "") == "user":
                pin_end = i + 1
                break

    pin_head = 0
    if pin_end > 0 and boundary == 0:
        pin_head = min(pin_end, len(active))
    elif pin_end > 0 and pin_end < boundary:
        pin_head = min(pin_end, len(active))

    cut = find_compaction_cut(active, pin_head=pin_head)
    if cut < 0:
        return None
    to_compact = active[pin_head:cut]
    retained = active[:pin_head] + active[cut:]
    if not to_compact:
        return None
    if pin_end > 0 and pin_end < boundary:
        new_boundary = boundary + (cut - pin_head)
    else:
        new_boundary = boundary + cut
    return to_compact, retained, new_boundary, summary, pin_end


def build_context_message_dicts(
    *,
    messages: list[dict],
    compaction_points: list[dict] | None,
    system_prompt: str,
    max_context_message_count: int,
    context_window: int,
    max_output: int | None = None,
    exclude_last: bool = False,
) -> tuple[str, list[dict]]:
    """
    构造将发给模型的逻辑消息列表（仍为 session 消息 dict）。
    exclude_last=True 时不包含最后一条（通常由调用方单独附加多模态 user）。
    返回 (summary, history_msgs)。
    """
    working = list(messages[:-1] if exclude_last and messages else messages)
    summary, active, _ = slice_after_compaction(working, compaction_points)
    limit_n = max_context_message_count
    if exclude_last and 0 < limit_n < 10_000_000:
        # 为当前 user 留 1 个名额
        active = [] if limit_n <= 1 else apply_message_count_limit(active, limit_n - 1)
    else:
        active = apply_message_count_limit(active, limit_n)

    sys_tokens = estimate_text_tokens(system_prompt.strip())
    if summary:
        sys_tokens += estimate_text_tokens(summary) + 8
    budget = available_window(context_window, max_output) if context_window > 0 else 0
    if budget > 0:
        headroom = 512 if exclude_last else 0
        active = trim_to_token_budget(active, max(1, budget - headroom), system_tokens=sys_tokens)
    return summary, active
