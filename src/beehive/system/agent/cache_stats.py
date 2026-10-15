"""DeepSeek 缓存命中观测（CacheHitTracker）与前缀护栏（PrefixGuard）。"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable


logger = logging.getLogger("wokbee")


def extract_cache_tokens(msg: Any) -> tuple[int, int]:
    """从 AIMessage 解析 (hit, miss)；无数据则 (0, 0)。"""
    hit = 0
    miss = 0

    um = getattr(msg, "usage_metadata", None)
    if isinstance(um, dict):
        details = um.get("input_token_details") or {}
        if isinstance(details, dict):
            hit = int(details.get("cache_read") or details.get("cache_read_tokens") or 0)
        inp = int(um.get("input_tokens") or 0)
        if inp and hit:
            miss = max(0, inp - hit)
        elif inp and not hit:
            miss = inp

    rm = getattr(msg, "response_metadata", None) or {}
    if not isinstance(rm, dict):
        rm = {}
    usage = rm.get("token_usage") or rm.get("usage") or {}
    if isinstance(usage, dict):
        if not hit:
            hit = int(
                usage.get("prompt_cache_hit_tokens")
                or usage.get("cache_hit_tokens")
                or 0
            )
        ds_miss = usage.get("prompt_cache_miss_tokens")
        if ds_miss is not None:
            miss = int(ds_miss)
        elif not miss:
            prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            if prompt:
                miss = max(0, prompt - hit)

    if isinstance(msg, dict):
        usage = msg.get("usage") or {}
        if isinstance(usage, dict):
            if not hit:
                hit = int(usage.get("prompt_cache_hit_tokens") or 0)
            if usage.get("prompt_cache_miss_tokens") is not None:
                miss = int(usage["prompt_cache_miss_tokens"])

    return max(0, hit), max(0, miss)


@dataclass
class CacheHitTracker:
    """会话级累计 hit/miss（Reasonix：本轮% + 会话 avg%）。"""

    hit_total: int = 0
    miss_total: int = 0
    last_hit: int = 0
    last_miss: int = 0
    turns: int = 0
    prefix_fp: str = ""
    on_update: Callable[[dict], None] | None = field(default=None, repr=False)

    def note_prefix(self, fp: str, tool_count: int) -> None:
        self.prefix_fp = fp
        self._emit_update(extra={"prefix_fp": fp, "tool_count": tool_count, "phase": "pin"})

    def observe_message(self, msg: Any) -> dict | None:
        hit, miss = extract_cache_tokens(msg)
        if hit <= 0 and miss <= 0:
            return None
        self.last_hit = hit
        self.last_miss = miss
        self.hit_total += hit
        self.miss_total += miss
        self.turns += 1
        payload = self.as_dict()
        self._emit_update(extra={"phase": "turn"})
        return payload

    def as_dict(self) -> dict:
        last_den = self.last_hit + self.last_miss
        sess_den = self.hit_total + self.miss_total
        return {
            "last_hit": self.last_hit,
            "last_miss": self.last_miss,
            "hit_total": self.hit_total,
            "miss_total": self.miss_total,
            "turns": self.turns,
            "now_pct": round(100.0 * self.last_hit / last_den) if last_den else None,
            "avg_pct": round(100.0 * self.hit_total / sess_den) if sess_den else None,
            "prefix_fp": self.prefix_fp,
        }

    def format_tag(self) -> str:
        d = self.as_dict()
        now_s = f"{d['now_pct']}%" if d["now_pct"] is not None else "—"
        avg_s = f"{d['avg_pct']}%" if d["avg_pct"] is not None else "—"
        return f"cache {now_s} · avg {avg_s}"

    def _emit_update(self, *, extra: dict | None = None) -> None:
        if not self.on_update:
            return
        payload = self.as_dict()
        if extra:
            payload.update(extra)
        try:
            self.on_update(payload)
        except Exception:
            logger.exception("cache tracker on_update 失败")


# --------------------------------------------------------------------------- #
# 前缀护栏（Prefix Guard）：Reasonix 式 append-only 检测
# --------------------------------------------------------------------------- #
# DeepSeek 自动前缀缓存不认 cache_control，只认「本请求前缀与上一请求逐字节一致」。
# 命中率低的根源几乎都是「静态前缀或消息历史被改写」，而非没开缓存。
# 本护栏在每次模型轮次结束后，对整条消息历史做 stable-JSON + SHA-256 逐下标哈希，
# 只在「旧前缀被改写」时告警（正常 append 不告警），并定位到具体消息。
# 对齐社区 pi-deepseek-cache 的 P2 前缀护栏：append-only、只在改写时告警。


from beehive.system.agent.prefix_guard import _message_role


from beehive.system.agent.prefix_guard import _message_content_text


from beehive.system.agent.prefix_guard import _message_fingerprint


from beehive.system.agent.prefix_guard import _stable_json


from beehive.system.agent.prefix_guard import PrefixGuard
