"""WokBee 正文预览限制及显示脱敏规则。"""
BUBBLE_PREVIEW_CHARS = 400
BUBBLE_PREVIEW_LINES = 10
BUBBLE_COLLAPSED_HEIGHT = 200
BUBBLE_COMPACT_HEIGHT = 22
BUBBLE_COMPACT_CHARS = 80


def _preview_text(full: str, *, max_chars: int = BUBBLE_PREVIEW_CHARS, max_lines: int = BUBBLE_PREVIEW_LINES) -> tuple[str, bool]:
    """返回 (预览文本, 是否被截断)。按行数与字数双重限制。"""
    text = full or ""
    if not text:
        return "", False
    lines = text.splitlines()
    truncated = False
    if len(lines) > max_lines:
        text = "\n".join(lines[:max_lines])
        truncated = True
    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True
    if truncated and not text.endswith("…"):
        text = text.rstrip() + "…"
    return text, truncated


def _redact_display(text: str) -> str:
    try:
        from beehive.system.credentials.credential_store import redact_text

        return redact_text(text or "")
    except Exception:
        return text or ""
