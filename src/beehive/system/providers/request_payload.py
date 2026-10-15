"""按已选择协议构建请求，不保存连接或取消状态。"""
import json
from beehive.system.providers.request_builder import build_completion_params
from beehive.system.providers.session_settings import SessionSettings


def responses_content(content, role: str) -> list[dict]:
    block_type = "output_text" if role == "assistant" else "input_text"
    if isinstance(content, str):
        return [{"type": block_type, "text": content}]
    if not isinstance(content, list):
        return [{"type": block_type, "text": str(content or "")}]
    result: list[dict] = []
    for part in content:
        if not isinstance(part, dict):
            result.append({"type": block_type, "text": str(part)})
            continue
        kind = part.get("type")
        if kind == "image_url":
            image = part.get("image_url") or {}
            url = image.get("url") if isinstance(image, dict) else image
            result.append({"type": "input_image", "image_url": url})
        elif kind in ("text", "input_text", "output_text"):
            result.append({"type": block_type, "text": str(part.get("text") or "")})
        elif part.get("text") is not None:
            result.append({"type": block_type, "text": str(part["text"])})
    return result or [{"type": block_type, "text": ""}]


def messages_to_responses(messages: list[dict]) -> tuple[str, list[dict]]:
    instructions: list[str] = []
    input_items: list[dict] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = message.get("content", "")
        if role == "system":
            if content:
                instructions.append(str(content) if isinstance(content, str) else json.dumps(content, ensure_ascii=False))
            continue
        if role == "tool":
            input_items.append({
                "type": "function_call_output",
                "call_id": str(message.get("tool_call_id") or ""),
                "output": str(content or ""),
            })
            continue
        input_items.append({
            "role": "assistant" if role == "assistant" else "user",
            "content": responses_content(content, role),
        })
    return "\n\n".join(instructions), input_items


def build_request_body(
    messages: list[dict],
    settings: SessionSettings | None = None,
    *,
    model: str,
    family: str,
    protocol: str,
    stream: bool = False,
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = "auto",
    temperature: float | None = None,
    top_p: float | None = None,
    max_tokens: int | None = None,
) -> dict:
    if settings is not None:
        params = build_completion_params(
            settings, model_id=model, family=family, stream=stream,
            api_protocol=protocol,
        )
        body = {
            "model": model,
            "messages": messages,
            **params,
        }
    else:
        body = {"model": model, "messages": messages}
        if stream:
            body["stream"] = True
        if temperature is not None:
            body["temperature"] = temperature
        if top_p is not None:
            body["top_p"] = top_p
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
    if protocol == "responses":
        instructions, input_items = messages_to_responses(messages)
        body.pop("messages", None)
        body["input"] = input_items
        if instructions:
            body["instructions"] = instructions
        if "max_tokens" in body:
            body["max_output_tokens"] = body.pop("max_tokens")
        # 兼容旧字段：reasoning_effort / thinking 一律转为 reasoning.effort
        body.pop("thinking", None)
        if "reasoning_effort" in body:
            body["reasoning"] = {"effort": body.pop("reasoning_effort")}
    if tools is not None:
        if protocol == "responses":
            converted = []
            for tool in tools:
                fn = tool.get("function", tool) if isinstance(tool, dict) else {}
                converted.append({"type": "function", **fn})
            body["tools"] = converted
            if tool_choice not in (None, "auto"):
                body["tool_choice"] = tool_choice
        else:
            body["tools"] = tools
            if tool_choice is not None:
                body["tool_choice"] = tool_choice
    return body
