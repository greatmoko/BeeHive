"""协调模型请求构建、传输与解析，保留聊天调用入口。"""
from collections.abc import Generator
from beehive.system.providers.session_settings import SessionSettings
from beehive.system.providers.response_types import ChatResponse, StreamChunk
from beehive.system.providers.request_payload import build_request_body
from beehive.system.providers.response_parser import parse_response, parse_stream
from beehive.system.providers.transport import AITransport


class AIClient:
    """普通与流式模型请求的协调入口。"""

    def __init__(self, endpoint: str, api_key: str, model: str, *,
                 family: str = "openai_compat", protocol: str = "chat",
                 retry_interval: int = 0, call_delay: float = 0):
        self._protocol = protocol if protocol in ("chat", "responses") else "chat"
        self._model = model
        self._family = family or "openai_compat"
        self._transport = AITransport(endpoint, api_key, self._protocol,
                                      retry_interval=retry_interval, call_delay=call_delay)

    @property
    def cancel_check(self):
        return self._transport.cancel_check

    @cancel_check.setter
    def cancel_check(self, value):
        self._transport.cancel_check = value

    @property
    def on_retry_log(self):
        return self._transport.on_retry_log

    @on_retry_log.setter
    def on_retry_log(self, value):
        self._transport.on_retry_log = value

    def chat(
        self,
        messages: list[dict],
        *,
        settings: SessionSettings | None = None,
        temperature: float | None = 0.7,
        top_p: float | None = 1.0,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = "auto",
        timeout: int = 180,
    ) -> ChatResponse:
        body = build_request_body(
            messages, settings, model=self._model, family=self._family, protocol=self._protocol, stream=False, tools=tools, tool_choice=tool_choice,
            temperature=temperature, top_p=top_p, max_tokens=max_tokens,
        )
        data = self._transport.request(body, timeout=timeout)
        return parse_response(data, self._protocol, self._model)

    def chat_stream(
        self,
        messages: list[dict],
        *,
        settings: SessionSettings | None = None,
        temperature: float | None = 0.7,
        top_p: float | None = 1.0,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = "auto",
    ) -> Generator[StreamChunk, None, None]:
        body = build_request_body(
            messages, settings, model=self._model, family=self._family, protocol=self._protocol, stream=True, tools=tools, tool_choice=tool_choice,
            temperature=temperature, top_p=top_p, max_tokens=max_tokens,
        )
        response = self._transport.open_stream(body)
        try:
            yield from parse_stream(response, self._protocol)
        finally:
            response.close()
