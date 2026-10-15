"""飞书REST 回复与会话发送回退。"""
from __future__ import annotations
import json
import logging
from beehive.system.gateway.base import ChannelMessage

logger = logging.getLogger("wokbee")


def send_text(self, msg: ChannelMessage, text: str) -> tuple[bool, str]:
    if not text:
        text = "（空回复）"
    if not (self._cfg.feishu_app_id and self._cfg.feishu_app_secret):
        return False, "未配置 app_id/app_secret"
    content = json.dumps({"text": text}, ensure_ascii=False)

    # 发送走独立的 REST 客户端：WS `Client` 没有 `.im` 接口，只能收发事件。
    # 复用已有实例（懒建），避免每次发消息都新建连接。
    rest = self._get_rest_client()
    if rest is None:
        return False, "REST 客户端初始化失败"

    def _reply_once() -> tuple[bool, str]:
        """先按消息回复，失败/无 message_id 再退化为按会话/私聊创建消息（更稳）。"""
        if msg.reply_to_message_id:
            try:
                from lark_oapi.api.im.v1 import (
                    ReplyMessageRequest,
                    ReplyMessageRequestBody,
                )
                body = (
                    ReplyMessageRequestBody.builder()
                    .content(content)
                    .msg_type("text")
                    .build()
                )
                req = (
                    ReplyMessageRequest.builder()
                    .message_id(msg.reply_to_message_id)
                    .request_body(body)
                    .build()
                )
                resp = rest.im.v1.message.reply(req)
                if resp is not None and getattr(resp, "code", -1) == 0:
                    return True, getattr(resp, "msg", "") or "ok"
            except Exception:  # noqa: BLE001
                logger.exception("按消息回复失败，退化为按会话发送")
        if msg.conversation_id:
            ok, err = _create_text(rest, content, "chat_id", msg.conversation_id)
            if ok:
                return True, err
            return False, err
        return False, "无 message_id 也无会话，无法回复"

    def _create_text(rest, content, receive_id_type, receive_id) -> tuple[bool, str]:
        from lark_oapi.api.im.v1 import (
            CreateMessageRequest,
            CreateMessageRequestBody,
        )
        body = (
            CreateMessageRequestBody.builder()
            .receive_id(receive_id)
            .content(content)
            .msg_type("text")
            .build()
        )
        req = (
            CreateMessageRequest.builder()
            .receive_id_type(receive_id_type)  # 注意：receive_id_type 在 Request 上
            .request_body(body)
            .build()
        )
        resp = rest.im.v1.message.create(req)
        if resp is not None and getattr(resp, "code", -1) == 0:
            return True, getattr(resp, "msg", "") or "ok"
        return False, getattr(resp, "msg", "") or f"code={getattr(resp, 'code', None)}"

    try:
        ok, err = _reply_once()
        if ok:
            return ok, err
        # 再兜底一次：用发送者 open_id 直接私聊（若会话建不了）
        if msg.sender_id:
            ok, err2 = _create_text(rest, content, "open_id", msg.sender_id)
            if ok:
                return ok, err2
            err = err2 or err
        return False, err
    except Exception as e:  # noqa: BLE001
        logger.exception("发送飞书消息失败")
        return False, str(e)
