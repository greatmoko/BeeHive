"""飞书消息文本解析。"""
from __future__ import annotations
import json
from beehive.system.gateway.base import strip_mentions


def extract_text(msg_type: str, content_str: str) -> str:
    """从飞书消息 content 里抠文本；图片/语音/文件等不支持。"""
    if msg_type == "text":
        try:
            body = json.loads(content_str or "{}")
            return strip_mentions(body.get("text", ""))
        except json.JSONDecodeError:
            return ""
    if msg_type == "post":
        try:
            body = json.loads(content_str or "{}")
            chunks = []
            for line in body.get("content") or []:
                for seg in line if isinstance(line, list) else []:
                    if isinstance(seg, dict) and seg.get("text"):
                        chunks.append(seg["text"])
            return strip_mentions("".join(chunks)) if chunks else ""
        except json.JSONDecodeError:
            return ""
    # 媒体类型：v1 不支持，交由 manager 回执提示
    return ""
