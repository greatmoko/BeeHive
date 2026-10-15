"""普通响应与 SSE 增量解析。"""
import json
from beehive.common.errors import AIError
from beehive.system.providers.response_types import ChatResponse, StreamChunk


def parse_responses(data: dict, model: str) -> ChatResponse:
    content = str(data.get("output_text") or "")
    reasoning_parts: list[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") == "output_text":
                    if not data.get("output_text"):
                        content += str(part.get("text") or "")
        elif item.get("type") == "reasoning":
            for part in item.get("summary") or []:
                if isinstance(part, dict):
                    reasoning_parts.append(str(part.get("text") or ""))
    return ChatResponse(
        content=content,
        reasoning_content="".join(reasoning_parts),
        model=data.get("model", model),
        usage=data.get("usage"),
    )


def parse_response(data: dict, protocol: str, model: str) -> ChatResponse:
    if protocol == "responses":
        return parse_responses(data, model)
    try:
        choice = data["choices"][0]
        message = choice["message"]
        raw_content = message.get("content")
        if raw_content is None:
            content = ""
        elif isinstance(raw_content, str):
            content = raw_content
        else:
            content = str(raw_content)
    except (KeyError, IndexError, TypeError) as e:
        raise AIError(f"API 返回格式异常: {data}") from e

    reasoning = message.get("reasoning_content") or ""
    tool_calls = message.get("tool_calls")
    if tool_calls is not None and not isinstance(tool_calls, list):
        tool_calls = None

    return ChatResponse(
        content=content,
        reasoning_content=reasoning,
        model=data.get("model", model),
        usage=data.get("usage"),
        tool_calls=tool_calls,
    )


def parse_stream(response, protocol: str):
    # 按字节缓冲，只对「完整行」解码：多字节 UTF-8 被切成两块时不再出现 �
    buf = b""
    for raw_bytes in response:
        buf += raw_bytes
        while b"\n" in buf:
            line_b, buf = buf.split(b"\n", 1)
            line = line_b.strip().decode("utf-8", errors="replace")
            if not line:
                continue
            if line.startswith("data:"):
                data_str = line[len("data:"):].strip()
                if data_str == "[DONE]":
                    yield StreamChunk(is_finished=True)
                    return
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                if protocol == "responses":
                    event_type = data.get("type")
                    if event_type == "response.output_text.delta":
                        delta = data.get("delta") or ""
                        if delta:
                            yield StreamChunk(content_delta=str(delta))
                    elif event_type in (
                        "response.reasoning_summary_text.delta",
                        "response.reasoning_text.delta",
                        "response.reasoning.delta",
                    ):
                        delta = data.get("delta") or ""
                        if delta:
                            yield StreamChunk(reasoning_delta=str(delta))
                    elif event_type in ("response.failed", "response.incomplete"):
                        details = data.get("response")
                        details = details if isinstance(details, dict) else {}
                        error = details.get("error") or {}
                        error = error if isinstance(error, dict) else {"message": str(error)}
                        reason = details.get("incomplete_details") or {}
                        reason = reason.get("reason") if isinstance(reason, dict) else str(reason)
                        raise AIError(
                            f"模型响应未完成（{event_type}）：{error.get('message') or reason or '服务端未提供错误详情'}",
                            code=str(error.get("code") or event_type),
                        )
                    elif event_type == "response.completed":
                        yield StreamChunk(is_finished=True)
                        return
                    continue
                choices = data.get("choices")
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                content_delta = delta.get("content") or ""
                reasoning_delta = delta.get("reasoning_content") or ""
                raw_delta = delta if delta else None
                if content_delta or reasoning_delta or delta.get("tool_calls"):
                    yield StreamChunk(
                        content_delta=content_delta,
                        reasoning_delta=reasoning_delta,
                        raw_delta=raw_delta,
                    )
    yield StreamChunk(is_finished=True)
