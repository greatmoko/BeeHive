"""模型前缀缓存观测与漂移事件展示。"""
from __future__ import annotations
import logging
from beehive.system.agent.cache_stats import CacheHitTracker
from beehive.system.agent.prefix_guard import PrefixGuard

logger = logging.getLogger("wokbee")

def configure_cache_events(self, fp, tool_names):
    def _on_cache_update(payload: dict) -> None:
        phase = payload.get("phase")
        if phase == "pin":
            self._emit(
                "info",
                f"缓存前缀已钉死（DeepSeek prefix-cache）：fp={payload.get('prefix_fp')}，"
                f"tools={payload.get('tool_count')}。"
                " system 本会话不变；项目态在用户消息【会话上下文】。",
                {"cache": True, **payload},
            )
            return
        tag = self._cache_tracker.format_tag()
        self._emit(
            "cache",
            tag,
            {"cache": True, **payload},
        )

    self._cache_tracker = CacheHitTracker(on_update=_on_cache_update)
    self._cache_tracker.note_prefix(fp, len(tool_names))

    # Reasonix 前缀护栏：只对 append-only 破坏告警，正常追加不打扰。
    def _on_prefix_drift(payload: dict) -> None:
        drift = payload.get("drift")
        if not isinstance(drift, dict):
            # 非漂移载荷（如发现点信息）不进改写告警，避免误报。
            return
        self._emit(
            "error",
            "缓存前缀被改写（append-only 破坏，DeepSeek 前缀缓存将在该点失效）：\n"
            f"位置 #{drift.get('index')} 类型 {drift.get('kind') or 'rewrite'} "
            f"role={drift.get('role') or '?'}\n"
            f"内容：{drift.get('content') or '（空）'}",
            {"cache": True, **payload},
        )

    self._prefix_guard = PrefixGuard(on_drift=_on_prefix_drift)
    self._prefix_guard.note_static(fp, len(tool_names))
