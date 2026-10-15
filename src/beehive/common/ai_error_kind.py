"""按职责组织的 ai_error_kind。"""
from __future__ import annotations
import enum


class AIErrorKind(enum.Enum):
    """AI API 错误的归一化分类。"""

    RATE_LIMIT = "rate_limit"
    """429 限速，瞬时可重试，宜按 Retry-After/退避重试。"""
    RATE_QUOTA = "rate_quota"
    """额度/余额耗尽（开源惯例：429 常同时携带 quota 语义；此处为 402 或明确 quota 提示）。不可自动重试。"""
    TRANSIENT = "transient"
    """网络/超时/连接类临时故障，瞬时可重试。"""
    SERVER = "server"
    """HTTP 5xx / 供应商上游故障，短暂后重试可能成功。"""
    CONTEXT_LENGTH = "context_length"
    """上下文超长（context_length_exceeded / token 超限），需压缩上下文后重试，不能硬重发同一请求。"""
    AUTH = "auth"
    """鉴权/密钥错误（401/403/invalid_api_key），不可自动重试，需用户修正 Key/权限。"""
    INVALID_REQUEST = "invalid_request"
    """请求参数/模型名/格式错误，不可自动重试，需用户修正请求。"""
    MODEL_NOT_FOUND = "model_not_found"
    """模型不存在/未启用（404 / model_not_found），需用户在厂商设置启用该模型。"""
    CONTENT_FILTER = "content_filter"
    """内容被供应商审核拦截，不可自动重试。"""
    UNSUPPORTED = "unsupported"
    """该模型/端点不支持此操作（如某些模型不接受部分参数）。可尝试降级或改参数。"""
    UNKNOWN = "unknown"
    """无法识别的错误，按未知处理，不自动重试。"""

    @property
    def retryable(self) -> bool:
        """是否应纳入自动重试。"""
        return self in (
            AIErrorKind.RATE_LIMIT,
            AIErrorKind.TRANSIENT,
            AIErrorKind.SERVER,
        )

    @property
    def advice(self) -> str:
        """面向用户的处置建议（中文）。"""
        return _ADVICE[self]


_ADVICE: dict[AIErrorKind, str] = {
    AIErrorKind.RATE_LIMIT: (
        "请求过频被限速（HTTP 429）。已按退避自动重试；若仍失败，请稍等片刻或降低"
        "并发/节流间隔（AI 节流设置在「设置」中调整），或联系厂商提升限额。"
    ),
    AIErrorKind.RATE_QUOTA: (
        "模型额度或账户余额已耗尽。请在厂商控制台充值/扩容或检查用量配额，"
        "不会自动重试。"
    ),
    AIErrorKind.TRANSIENT: (
        "网络连接/超时等临时故障。已自动重试；若持续失败，请检查网络与 API Host 可达性。"
    ),
    AIErrorKind.SERVER: (
        "模型服务端返回错误（HTTP 5xx / 上游故障）。已短暂退避后重试；"
        "若连续出现，多为厂家侧问题，可稍候再试或到厂商状态页查看。"
    ),
    AIErrorKind.CONTEXT_LENGTH: (
        "上下文超长（context_length_exceeded）。已尝试在压缩后重发；"
        "若仍失败，请缩短本轮输入/减少历史消息，或改用更大上下文窗口的模型。"
    ),
    AIErrorKind.AUTH: (
        "鉴权失败（HTTP 401/403，或 Key 无效/对模型无权限）。请在「厂商设置」检查"
        " API Key 是否正确、模型是否对该 Key 开放；不会自动重试。"
    ),
    AIErrorKind.INVALID_REQUEST: (
        "请求参数不正确（参数非法/格式错误/字段不兼容）。多在调用自研客户端或使用"
        "高版本协议时触发；请核对请求参数与模型能力。"
    ),
    AIErrorKind.MODEL_NOT_FOUND: (
        "未找到模型 / 模型未启用（HTTP 404 / model_not_found）。请在「厂商设置」启用"
        "或更换正确模型 ID；不会自动重试。"
    ),
    AIErrorKind.CONTENT_FILTER: (
        "内容被供应商审核拦截（content_filter）。不会自动重试，请调整提问或输出内容。"
    ),
    AIErrorKind.UNSUPPORTED: (
        "该模型/端点不支持此请求（如推理模型不接受 temperature 等采样参数，或字段不兼容）。"
        "请在「AI配置」调整该模型的进参/适配器设置。"
    ),
    AIErrorKind.UNKNOWN: (
        "未识别的模型错误。请结合下方原始信息排查，必要时到厂商后台用 request id 查日志。"
    ),
}
