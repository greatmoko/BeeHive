"""按职责组织的 slicing。"""
from __future__ import annotations
from beehive.system.memory.context.tokens import estimate_message_tokens
from beehive.system.memory.context.tokens import estimate_messages_tokens
RETAIN_RECENT_RATIO = 0.4  # 压缩时保留最近约 40% 原文


def latest_compaction(points: list[dict] | None) -> dict | None:
    if not points:
        return None
    return points[-1]


def slice_after_compaction(
    messages: list[dict],
    points: list[dict] | None,
) -> tuple[str, list[dict], int]:
    """返回 (summary, active_messages, boundary_index)。

    若 compaction point 带 pin_end（Reasonix 式钉住前缀），则保留
    messages[:pin_end] 不被 boundary 吃掉，避免压缩破坏稳定任务头。
    """
    cp = latest_compaction(points)
    if not cp:
        return "", list(messages), 0
    try:
        boundary = int(cp.get("boundary_index", 0))
    except (TypeError, ValueError):
        boundary = 0
    try:
        pin_end = int(cp.get("pin_end", 0) or 0)
    except (TypeError, ValueError):
        pin_end = 0
    boundary = max(0, min(boundary, len(messages)))
    pin_end = max(0, min(pin_end, len(messages)))
    summary = str(cp.get("summary") or "").strip()
    if pin_end > 0 and pin_end < boundary:
        return summary, list(messages[:pin_end]) + list(messages[boundary:]), boundary
    return summary, list(messages[boundary:]), boundary


def apply_message_count_limit(messages: list[dict], max_count: int) -> list[dict]:
    if max_count <= 0 or max_count >= 10_000_000:
        return list(messages)
    if len(messages) <= max_count:
        return list(messages)
    return list(messages[-max_count:])


def trim_to_token_budget(
    messages: list[dict],
    budget: int,
    *,
    system_tokens: int = 0,
) -> list[dict]:
    """从最旧非 system 消息开始丢弃，直到落入 budget。"""
    if budget <= 0:
        return list(messages)
    msgs = list(messages)
    used = system_tokens + estimate_messages_tokens(msgs)
    while used > budget and msgs:
        # 尽量在 user 边界丢弃一整轮
        drop_to = 1
        if msgs[0].get("role") == "user":
            drop_to = 1
            if len(msgs) > 1 and msgs[1].get("role") == "assistant":
                drop_to = 2
        else:
            drop_to = 1
        for _ in range(min(drop_to, len(msgs))):
            dropped = msgs.pop(0)
            used -= estimate_message_tokens(dropped)
    return msgs


def find_compaction_cut(
    active: list[dict],
    retain_ratio: float = RETAIN_RECENT_RATIO,
    *,
    pin_head: int = 0,
) -> int:
    """返回 active 内的切分下标：[pin_head:cut] 将被摘要，钉住头 + [cut:] 保留。

    pin_head：保留开头若干条（通常为首条任务 user），对齐 Reasonix pinned prefix。
    """
    pin_head = max(0, int(pin_head or 0))
    if len(active) < 4 + pin_head:
        return -1
    retain = max(2, int(len(active) * retain_ratio))
    cut = len(active) - retain
    while cut > pin_head and active[cut].get("role") != "user":
        cut -= 1
    if cut <= pin_head:
        return -1
    return cut
