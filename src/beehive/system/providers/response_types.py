"""模型请求结果及流式增量。"""
from dataclasses import dataclass


@dataclass
class ChatResponse:
    content: str
    reasoning_content: str = ""
    model: str = ""
    usage: dict | None = None
    tool_calls: list[dict] | None = None


@dataclass
class StreamChunk:
    """流式响应的单个增量片段。"""
    content_delta: str = ""
    reasoning_delta: str = ""
    is_finished: bool = False
    raw_delta: dict | None = None
