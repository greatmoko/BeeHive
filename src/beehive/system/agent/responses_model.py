"""Responses 加密历史失效时的一次性无状态恢复，不修改 checkpoint。"""

import logging

from langchain_openai import ChatOpenAI
from openai import BadRequestError
from pydantic import PrivateAttr

logger = logging.getLogger("wokbee")


class ResponsesChatOpenAI(ChatOpenAI):
    """保留有效推理状态，仅在服务端明确拒绝密文时降级历史回传。"""

    _replay_without_encrypted: bool = PrivateAttr(default=False)

    def _recover_encrypted_history(self, error: BadRequestError) -> bool:
        if self._replay_without_encrypted or error.code != "invalid_encrypted_content":
            return False
        self._replay_without_encrypted = True
        logger.warning("Responses 历史推理项校验失败，保留对话正文与工具结果后重试一次。")
        return True

    def _get_request_payload(self, *args, **kwargs):
        payload = super()._get_request_payload(*args, **kwargs)
        if self._replay_without_encrypted:
            payload["input"] = [
                {key: value for key, value in item.items() if key != "id"}
                for item in payload.get("input", [])
                if item.get("type") not in ("reasoning", "compaction", "item_reference")
            ]
            payload.pop("previous_response_id", None)
            payload["store"] = False
        return payload

    def _generate(self, *args, **kwargs):
        try:
            return super()._generate(*args, **kwargs)
        except BadRequestError as error:
            if not self._recover_encrypted_history(error):
                raise
        return super()._generate(*args, **kwargs)

    async def _agenerate(self, *args, **kwargs):
        try:
            return await super()._agenerate(*args, **kwargs)
        except BadRequestError as error:
            if not self._recover_encrypted_history(error):
                raise
        return await super()._agenerate(*args, **kwargs)

    def _stream(self, *args, **kwargs):
        emitted = False
        try:
            for chunk in super()._stream(*args, **kwargs):
                emitted = True
                yield chunk
            return
        except BadRequestError as error:
            if emitted or not self._recover_encrypted_history(error):
                raise
        yield from super()._stream(*args, **kwargs)

    async def _astream(self, *args, **kwargs):
        emitted = False
        try:
            async for chunk in super()._astream(*args, **kwargs):
                emitted = True
                yield chunk
            return
        except BadRequestError as error:
            if emitted or not self._recover_encrypted_history(error):
                raise
        async for chunk in super()._astream(*args, **kwargs):
            yield chunk
