"""按职责组织的 prefix_guard。"""
from __future__ import annotations
import logging
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable
logger = logging.getLogger("wokbee")


def _message_role(msg: Any) -> str:
    if isinstance(msg, dict):
        return str(msg.get("role") or msg.get("type") or "")
    return str(getattr(msg, "type", None) or "")


def _message_content_text(content: Any) -> str:
    """把 str / list[block] / dict 统一成纯文本，稳定参与哈希。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                t = block.get("type")
                if t == "text":
                    parts.append(str(block.get("text") or ""))
                elif t == "image":
                    parts.append("[image]")
                elif isinstance(block.get("content"), (str, list)):
                    parts.append(_message_content_text(block.get("content")))
                else:
                    parts.append(str(block))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    if isinstance(content, dict):
        return _message_content_text(content.get("content") or content)
    return str(content)


def _message_fingerprint(msg: Any) -> str:
    """单条消息 → stable-JSON 哈希。只取会上行模型的关键字段，不含 metadata。"""
    if isinstance(msg, dict):
        role = _message_role(msg)
        payload = {
            "role": role,
            "content": _message_content_text(msg.get("content")),
        }
        if msg.get("name"):
            payload["name"] = str(msg["name"])
        if msg.get("tool_call_id"):
            payload["tool_call_id"] = str(msg["tool_call_id"])
        tcs = msg.get("tool_calls")
        if tcs:
            normalized = []
            for tc in tcs:
                if hasattr(tc, "get"):
                    function = tc.get("function") or {}
                    normalized.append(
                        {
                            "id": str(tc.get("id") or ""),
                            "name": str(tc.get("name") or function.get("name") or ""),
                            "args": _stable_json(tc.get("args") or function.get("arguments") or function),
                        }
                    )
                else:
                    normalized.append(
                        {
                            "id": str(getattr(tc, "id", "") or ""),
                            "name": str(getattr(tc, "name", "") or ""),
                            "args": _stable_json(getattr(tc, "args", None) or {}),
                        }
                    )
            payload["tool_calls"] = normalized
    else:
        payload = {
            "role": _message_role(msg),
            "content": _message_content_text(getattr(msg, "content", None)),
        }
        name = getattr(msg, "name", None)
        if name:
            payload["name"] = str(name)
        tcid = getattr(msg, "tool_call_id", None)
        if tcid:
            payload["tool_call_id"] = str(tcid)
        tcs = getattr(msg, "tool_calls", None) or getattr(msg, "additional_kwargs", {}).get("tool_calls")
        if tcs:
            normalized = []
            for tc in tcs:
                if hasattr(tc, "get"):
                    function = tc.get("function") or {}
                    normalized.append(
                        {
                            "id": str(tc.get("id") or ""),
                            "name": str(tc.get("name") or function.get("name") or ""),
                            "args": _stable_json(tc.get("args") or function.get("arguments") or {}),
                        }
                    )
                else:
                    normalized.append({"id": str(getattr(tc, "id", "") or ""), "name": str(getattr(tc, "name", "") or "")})
            payload["tool_calls"] = normalized
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _stable_json(obj: Any) -> Any:
    """尽力把 args/function 归一化成可稳定序列化的 JSON 值。"""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_stable_json(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _stable_json(v) for k, v in obj.items()}
    return str(obj)


@dataclass
class PrefixGuard:
    """会话级前缀护栏：检测消息历史 append-only 是否被破坏。

    复用 Reasonix 的「turn-end cap + 只追加」不变量：正常往尾部追加新消息
    不会触发告警；一旦某个已出现下标的前缀哈希改变（改写/重排/压缩/就地修改），
    即上报改写点，用于归因命中率骤降。
    """

    _seen: dict[int, str] = field(default_factory=dict)  # index -> 前缀哈希
    _count: int = 0
    _fprs: list[str] = field(default_factory=list, repr=False)  # index -> 指纹（缓存）
    static_fp: str = ""
    on_drift: Callable[[dict], None] | None = field(default=None, repr=False)

    def note_static(self, fp: str, tool_count: int) -> None:
        # 只存静态指纹；钉死/前缀信息由 CacheHitTracker 的 pin 事件展示，
        # 不把「无 drift」的载荷送进 on_drift，避免误报改写。
        self.static_fp = fp

    def check(self, messages: list) -> dict:
        """对整条消息历史做 append-only 校验（增量）。

        返回 {"ok": bool, "drift": dict|None, "count": int}；发现改写时先上报再返回。
        纯追加（数量增加）时复用缓存指纹、只重算新增下标，避免每轮对整条历史全量
        重哈希（会话越长开销越大的问题）；数量未变（可能就地改写）或缩短时全量重算以
        检测 drift。
        """
        if not messages:
            return {"ok": True, "drift": None, "count": self._count}

        n = len(messages)
        if n > self._count and len(self._fprs) == self._count:
            # 纯追加：复用旧段指纹，只算新增尾部（sha256 逐 index 重算仍便宜，
            # 唯一变贵的是 _message_fingerprint，已通过缓存规避）
            fprs = self._fprs + [_message_fingerprint(m) for m in messages[self._count:]]
        else:
            # 数量未变（可能改写）或缩短：全量重算以检测 rewrite/truncated
            fprs = [_message_fingerprint(m) for m in messages]

        hasher = hashlib.sha256()
        current: dict[int, str] = {}
        drift: dict | None = None

        for i, fp in enumerate(fprs):
            hasher.update(fp.encode("utf-8"))
            current[i] = hasher.hexdigest()[:20]
            if drift is None and i in self._seen and self._seen[i] != current[i]:
                drift = {
                    "index": i,
                    "role": _message_role(messages[i]),
                    "content": _message_content_text(
                        messages[i].get("content") if isinstance(messages[i], dict)
                        else getattr(messages[i], "content", "")
                    )[:160],
                    "kind": "rewrite",
                }

        # 历史缩短：原地删/切中间消息也算破坏前缀
        if drift is None and messages and len(fprs) < self._count:
            drift = {
                "index": len(fprs),
                "role": "eof",
                "content": f"消息数由 {self._count} 缩为 {len(fprs)}",
                "kind": "truncated",
            }

        self._seen = current
        self._count = len(fprs)
        self._fprs = fprs

        result = {"ok": drift is None, "drift": drift, "count": len(fprs)}
        if drift is not None:
            self._report_extra({"phase": "drift", **result})
        return result

    def _report_extra(self, extra: dict) -> None:
        if not self.on_drift:
            return
        try:
            self.on_drift(extra)
        except Exception:
            logger.exception("prefix guard on_drift 失败")
