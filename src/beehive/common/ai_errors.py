"""统一 AI API 错误分类。

把来自不同来源的 AI 调用异常（openai SDK 新式异常、httpx 网络错误、自研
AIClient 的 raw HTTPError、供应商返回体里的错误类型/错误码、以及纯字符串特征）
归一到 `AIErrorKind` 枚举，每个分类附带处置建议（中文）。

处置策略遵循开源社区（openai SDK / langchain-openai / LiteLLM）的通行约定：

- 瞬时可自动重试：429 限速、HTTP 5xx、超时、连接重置等临时故障 —— 退避重试
- 不可自动重试：鉴权失败 / 额度耗尽 / 上下文超长 —— 终止并向用户给出定位与建议
- ``AIErrorKind.retryable`` 为 True 的分类才应被自动重试逻辑采纳
"""
from __future__ import annotations

import enum
import re
from typing import Any

# 试图构造 openai 异常类时若 openai 未安装则静默跳过（分类器应健壮，不因缺依赖炸）。
try:  # pragma: no cover - 依赖可选
    from openai._exceptions import (  # type: ignore[attr-defined]
        APIError as _OA_APIError,
        APIStatusError as _OA_APIStatusError,
        APITimeoutError as _OA_APITimeoutError,
        APIConnectionError as _OA_APIConnectionError,
        AuthenticationError as _OA_AuthenticationError,
        BadRequestError as _OA_BadRequestError,
        ConflictError as _OA_ConflictError,
        ContentFilterFinishReasonError as _OA_ContentFilter,
        InternalServerError as _OA_InternalServerError,
        NotFoundError as _OA_NotFoundError,
        PermissionDeniedError as _OA_PermissionDeniedError,
        RateLimitError as _OA_RateLimitError,
        UnprocessableEntityError as _OA_UnprocessableEntityError,
    )
except Exception:  # pragma: no cover
    _OA_APIError = None
    _OA_APIStatusError = None
    _OA_APITimeoutError = None
    _OA_APIConnectionError = None
    _OA_AuthenticationError = None
    _OA_BadRequestError = None
    _OA_ConflictError = None
    _OA_ContentFilter = None
    _OA_InternalServerError = None
    _OA_NotFoundError = None
    _OA_PermissionDeniedError = None
    _OA_RateLimitError = None
    _OA_UnprocessableEntityError = None


from beehive.common.ai_error_kind import AIErrorKind



# 厂商返回体里常用的错误 (type, code)，映射到分类（按常见性）。字符串片段原文保留小写比较。
_TYPE_HINTS: list[tuple[AIErrorKind, tuple[str, ...]]] = [
    (AIErrorKind.RATE_LIMIT, ("rate_limit", "requests_rate_limited", "rate_limited")),
    (AIErrorKind.AUTH, ("authentication_error", "invalid_api_key", "invalid authentication", "access denied", "forbidden", "unauthorized", "permission_denied")),
    (AIErrorKind.MODEL_NOT_FOUND, ("model_not_found", "invalid_request_error::model", "model does not exist", "unknown model")),
    (AIErrorKind.CONTEXT_LENGTH, ("context_length_exceeded", "request_too_large", "tokens_limit_reached", "maximum context length", "context window", "too many tokens", "token limit")),
    (AIErrorKind.CONTENT_FILTER, ("content_filter", "safety", "prompt was blocked", "moderation")),
    (AIErrorKind.INVALID_REQUEST, ("invalid_request_error", "invalid_request", "bad_request", "param_error", "invalid parameter", "unsupported_parameter")),
    (AIErrorKind.RATE_QUOTA, ("insufficient_quota", "insufficient quota", "quota exhausted", "billing", "out of credits", "no balance", "account balance", "balance_insufficient", "payment required")),
    (AIErrorKind.UNSUPPORTED, ("not_supported", "unsupported", "does not support", "not_implemented")),
]

# HTTP 状态码 → 分类兜底（仅在错误携带 status_code 且上面无更具体的 type 命中时使用）
_STATUS_FALLBACK = {
    400: AIErrorKind.INVALID_REQUEST,
    401: AIErrorKind.AUTH,
    402: AIErrorKind.RATE_QUOTA,
    403: AIErrorKind.AUTH,
    404: AIErrorKind.MODEL_NOT_FOUND,
    422: AIErrorKind.INVALID_REQUEST,
    429: AIErrorKind.RATE_LIMIT,
    500: AIErrorKind.SERVER,
    502: AIErrorKind.SERVER,
    503: AIErrorKind.SERVER,
    504: AIErrorKind.SERVER,
}


def _status_code(exc: BaseException) -> int | None:
    """从格式各异的异常里尽量取出 HTTP 状态码。"""
    if isinstance(exc, _OA_APIStatusError) and exc is not None:
        try:
            code = int(exc.status_code)
            return code
        except Exception:
            return None
    # 兼容旧 openai / 自研异常带 .status_code 或 .resp.code 等
    code = getattr(exc, "status_code", None)
    if code is not None:
        try:
            return int(code)
        except Exception:
            pass
    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            code = getattr(resp, "status_code", None) or getattr(resp, "code", None)
            return int(code)
        except Exception:
            pass
    return None


def _body_text(exc: BaseException) -> str:
    """拼出可检索的原始错误文本（含 message / type / code / response body）。"""
    parts: list[str] = []
    for attr in ("message", "code", "type"):
        v = getattr(exc, attr, None)
        if v:
            parts.append(str(v))
    if isinstance(exc, _OA_APIStatusError) and exc is not None:
        body = getattr(exc, "body", None)
        if body:
            parts.append(str(body)[:4000])
    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            text = getattr(resp, "text", None)
            if not text:
                data = getattr(resp, "json", lambda: None)()
                text = str(data) if data else ""
            if text:
                parts.append(str(text)[:4000])
        except Exception:
            pass
    return "\n".join(p for p in parts if p)


def classify_error(exc: BaseException) -> AIErrorKind:
    """把任意 AI 调用异常归一化为 AIErrorKind。

    优先级：
    1. openai 新式异常的具体类型 → 2. 原始类型/编码字符串特征 →
       3. HTTP status_code 兜底 → 4. 内部异常链向上追溯（包装层常见）。
    """
    if exc is None:
        return AIErrorKind.UNKNOWN

    # 1) openai 新式异常类型
    if _OA_RateLimitError is not None and isinstance(exc, _OA_RateLimitError):
        return AIErrorKind.RATE_LIMIT
    if _OA_ContentFilter is not None and isinstance(exc, _OA_ContentFilter):
        return AIErrorKind.CONTENT_FILTER
    if _OA_APITimeoutError is not None and isinstance(exc, _OA_APITimeoutError):
        return AIErrorKind.TRANSIENT
    if _OA_APIConnectionError is not None and isinstance(exc, _OA_APIConnectionError):
        return AIErrorKind.TRANSIENT
    if _OA_AuthenticationError is not None and isinstance(exc, _OA_AuthenticationError):
        return AIErrorKind.AUTH
    if _OA_PermissionDeniedError is not None and isinstance(exc, _OA_PermissionDeniedError):
        return AIErrorKind.AUTH
    if _OA_NotFoundError is not None and isinstance(exc, _OA_NotFoundError):
        return AIErrorKind.MODEL_NOT_FOUND
    if _OA_InternalServerError is not None and isinstance(exc, _OA_InternalServerError):
        return AIErrorKind.SERVER
    if _OA_ConflictError is not None and isinstance(exc, _OA_ConflictError):
        return AIErrorKind.RATE_LIMIT
    if _OA_UnprocessableEntityError is not None and isinstance(exc, _OA_UnprocessableEntityError):
        return AIErrorKind.INVALID_REQUEST
    if _OA_BadRequestError is not None and isinstance(exc, _OA_BadRequestError):
        return classify_text_or_status(_body_text(exc), _status_code(exc))
    if _OA_APIStatusError is not None and isinstance(exc, _OA_APIStatusError):
        return _status_error(exc)

    # 2) 字符串特征 + 状态码
    kind = classify_text_or_status(_body_text(exc), _status_code(exc))

    # 3) 内部异常链向上追溯
    if kind == AIErrorKind.UNKNOWN:
        cause = exc.__cause__ or exc.__context__
        if cause is not None and cause is not exc:
            return classify_error(cause)
    return kind


def _status_error(exc: BaseException) -> AIErrorKind:
    """APIStatusError 兜底：先在 body 文本里找 type/code 特征，再按状态码。"""
    kind = classify_text(text)
    if kind != AIErrorKind.UNKNOWN:
        return kind
    code = _status_code(exc)
    return _STATUS_FALLBACK.get(code, AIErrorKind.UNKNOWN)


def classify_text_or_status(text: str, status_code: int | None) -> AIErrorKind:
    kind = classify_text(text)
    if kind != AIErrorKind.UNKNOWN:
        return kind
    return _STATUS_FALLBACK.get(status_code, AIErrorKind.UNKNOWN) if status_code else AIErrorKind.UNKNOWN


def classify_text(text: str) -> AIErrorKind:
    """仅凭错误文本特征分类（self._body_text 先看 type/code/message）。"""
    if not text:
        return AIErrorKind.UNKNOWN
    low = text.lower()
    for kind, hints in _TYPE_HINTS:
        for hint in hints:
            if hint in low:
                return kind
    return AIErrorKind.UNKNOWN


def http_status_to_kind(status: int) -> AIErrorKind:
    return _STATUS_FALLBACK.get(int(status), AIErrorKind.UNKNOWN)


def display_message(kind: AIErrorKind, original: str = "") -> str:
    """生成面向用户的可读错误描述：分类建议 + 原始信息。"""
    base = getattr(kind, "advice", AIErrorKind.UNKNOWN.advice)
    if original:
        return f"{base}\n原始信息：{original}"
    return base


# ---- AIError 携带分类（自研客户端/内部抛出时附加） ----


def attach_kind(exc: BaseException | None, kind: AIErrorKind) -> BaseException | None:
    """若异常对象支持则把 kind 挂上去；否则返回原对象（不抛新异常）。"""
    if exc is None:
        return None
    try:
        if hasattr(exc, "ai_kind"):
            try:
                exc.ai_kind = kind  # type: ignore[attr-assign]
            except Exception:
                pass
        return exc
    except Exception:
        return exc
