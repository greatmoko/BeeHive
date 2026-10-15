"""按职责组织的 tokens。"""
from __future__ import annotations
from typing import Any
OUTPUT_RESERVE_TOKENS = 4096
DEFAULT_COMPACTION_THRESHOLD = 0.6
TOKENS_PER_IMAGE = 1600


def estimate_text_tokens(text: str) -> int:
    if not text:
        return 0
    # CJK 感知：中文等全角字符约 1 字 = 1 token，其余仍按英文启发式 (n+3)//4。
    # 旧值把中文按 4 字=1 token 低估约 4 倍，导致压缩/裁剪启动过晚。
    cjk = 0
    others = 0
    for ch in text:
        cp = ord(ch)
        if (
            0x4E00 <= cp <= 0x9FFF  # CJK 统一表意
            or 0x3400 <= cp <= 0x4DBF  # 扩展 A
            or 0x3000 <= cp <= 0x303F  # CJK 标点
            or 0xFF00 <= cp <= 0xFFEF  # 全角符号/字母
            or 0x3040 <= cp <= 0x30FF  # 日文假名
            or 0xAC00 <= cp <= 0xD7AF  # 韩文
        ):
            cjk += 1
        else:
            others += 1
    return max(1, cjk + (others + 3) // 4)


def estimate_content_tokens(content: Any) -> int:
    if content is None:
        return 0
    if isinstance(content, str):
        return estimate_text_tokens(content)
    if isinstance(content, list):
        total = 0
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "image_url" or "image_url" in part:
                total += TOKENS_PER_IMAGE
            else:
                total += estimate_text_tokens(str(part.get("text") or ""))
        return total
    return estimate_text_tokens(str(content))


def estimate_message_tokens(msg: dict) -> int:
    tokens = estimate_content_tokens(msg.get("content"))
    atts = msg.get("attachments") or []
    for att in atts:
        if isinstance(att, dict):
            path = str(att.get("path") or "")
            name = str(att.get("name") or path)
        else:
            path = str(att)
            name = path
        lower = path.lower()
        if any(lower.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")):
            tokens += TOKENS_PER_IMAGE
        else:
            tokens += estimate_text_tokens(name) + 32
    # role / formatting overhead
    return tokens + 4


def estimate_messages_tokens(messages: list[dict]) -> int:
    return sum(estimate_message_tokens(m) for m in messages)


def available_window(context_window: int, max_output: int | None = None) -> int:
    if context_window <= 0:
        return 0
    reserve = max_output if (max_output and max_output > 0) else OUTPUT_RESERVE_TOKENS
    reserve = min(reserve, max(1, context_window // 2))
    return max(context_window - reserve, context_window // 2)


def threshold_tokens(
    context_window: int,
    compaction_threshold: float = DEFAULT_COMPACTION_THRESHOLD,
    max_output: int | None = None,
) -> int:
    avail = available_window(context_window, max_output)
    if avail <= 0:
        return 0
    thr = compaction_threshold if compaction_threshold > 0 else DEFAULT_COMPACTION_THRESHOLD
    thr = min(max(thr, 0.1), 0.95)
    return max(1, int(avail * thr))
