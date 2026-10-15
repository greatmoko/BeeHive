"""HTTP 错误解析与可重试错误判断。"""
import json
import urllib.error
from beehive.common.errors import AIError


def handle_http_error(e: urllib.error.HTTPError):
    error_body = e.read().decode("utf-8", errors="replace")
    try:
        error_data = json.loads(error_body)
        err = error_data.get("error", {}) if isinstance(error_data, dict) else {}
        if isinstance(err, dict):
            msg = err.get("message") or error_body[:300]
            code = err.get("code")
        else:
            msg = str(err)[:300]
            code = None
    except (json.JSONDecodeError, AttributeError):
        msg = error_body[:300]
        code = None
    try:
        from beehive.common.ai_errors import classify_text_or_status

        kind = classify_text_or_status(f"{msg}", e.code)
    except Exception:
        kind = None
    raise AIError(
        f"API 错误 (HTTP {e.code}): {msg}",
        kind=kind,
        status_code=e.code,
        code=str(code) if code is not None else None,
    ) from e


def is_retryable(exc: Exception) -> bool:
    if isinstance(exc, ConnectionResetError):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (ConnectionResetError, ConnectionAbortedError, TimeoutError, OSError)):
            return True
    if isinstance(exc, (TimeoutError, ConnectionAbortedError)):
        return True
    if isinstance(exc, OSError) and getattr(exc, "winerror", 0) in (10054, 10053):
        return True
    return False
