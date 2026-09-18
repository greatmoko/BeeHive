"""Provider 数据模型与持久化序列化。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict

from tokbee.core.provider import ProviderModelDef
from tokbee.core.provider_secrets import _open_key, _seal_key

def _migrate_reasoning_adapter(d: dict) -> str:
    """旧字段迁移 → 新 reasoning_adapter：""=自动 / openai / deepseek。"""
    a = str(d.get("reasoning_adapter") or "").strip().lower()
    if a in ("openai", "deepseek"):
        return a
    ctrl = str(d.get("reasoning_control") or "").strip().lower()
    if ctrl in ("thinking", "enable_thinking"):
        return "deepseek"
    if ctrl in ("reasoning_effort", "thinking_config"):
        return "openai"
    return ""


def _migrate_reasoning_effort(d: dict) -> str:
    """旧字段迁移 → 新 reasoning_effort。"""
    e = str(d.get("reasoning_effort") or "").strip()
    if e:
        return e
    adapter = _migrate_reasoning_adapter(d)
    if adapter == "deepseek":
        return str(d.get("deepseek_reasoning_effort") or "").strip()
    return str(d.get("openai_reasoning_effort") or "").strip()


@dataclass
class ProviderModel:
    model_id: str
    nickname: str = ""
    capabilities: list[str] = field(default_factory=list)
    context_window: int = 1_000_000
    max_output: int = 0
    enabled: bool = False  # 默认不勾选，由用户启用
    api_protocol: str = "chat"  # chat | responses
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stream: bool = True
    reasoning_enabled: bool = True
    reasoning_adapter: str = ""  # "", "openai", "deepseek"
    reasoning_effort: str = "medium"

    @classmethod
    def from_def(cls, d: ProviderModelDef, enabled: bool = False) -> "ProviderModel":
        return cls(
            model_id=d.model_id,
            nickname=d.nickname,
            capabilities=list(d.capabilities),
            context_window=d.context_window,
            max_output=d.max_output,
            enabled=enabled,
            api_protocol="chat",
        )

    @classmethod
    def from_dict(cls, d: dict) -> "ProviderModel":
        return cls(
            model_id=str(d.get("model_id", "")),
            nickname=str(d.get("nickname", "")),
            capabilities=list(d.get("capabilities") or []),
            context_window=int(d.get("context_window") or 1_000_000),
            max_output=int(d.get("max_output") or 0),
            enabled=bool(d.get("enabled", False)),
            api_protocol=(
                str(d.get("api_protocol") or "chat").strip().lower()
                if str(d.get("api_protocol") or "chat").strip().lower() in ("chat", "responses")
                else "chat"
            ),
            temperature=_optional_float(d.get("temperature")),
            top_p=_optional_float(d.get("top_p")),
            max_tokens=_optional_int(d.get("max_tokens")),
            stream=bool(d.get("stream", True)),
            reasoning_enabled=bool(d.get("reasoning_enabled", True)),
            reasoning_adapter=_migrate_reasoning_adapter(d),
            reasoning_effort=_migrate_reasoning_effort(d) or "medium",
        )


def _optional_float(value, default: float | None = None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass
class ProviderSettings:
    api_key: str = ""
    api_host: str = ""
    models: list[ProviderModel] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "api_key": _seal_key(self.api_key),
            "api_host": self.api_host,
            "models": [asdict(m) for m in self.models],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProviderSettings":
        models = [ProviderModel.from_dict(m) for m in (d.get("models") or [])]
        return cls(
            api_key=_open_key(str(d.get("api_key", ""))),
            api_host=str(d.get("api_host", "")),
            models=models,
        )


@dataclass
class CustomProviderInfo:
    id: str = field(default_factory=lambda: f"custom-{uuid.uuid4().hex[:8]}")
    name: str = "自定义本地 API"
    icon: str = "🖥️"
    family: str = "openai_compat"
    notes: str = "自定义 OpenAI 兼容本地 / 私有 API"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CustomProviderInfo":
        return cls(
            id=str(d.get("id") or f"custom-{uuid.uuid4().hex[:8]}"),
            name=str(d.get("name") or "自定义本地 API"),
            icon=str(d.get("icon") or "🖥️"),
            family=str(d.get("family") or "openai_compat"),
            notes=str(d.get("notes") or "自定义 OpenAI 兼容本地 / 私有 API"),
        )


@dataclass
class ResolvedModel:
    """对话调用时解析出的当前模型连接信息。"""
    provider_id: str
    provider_name: str
    model_id: str
    api_host: str
    api_key: str
    family: str
    context_window: int = 1_000_000
    api_protocol: str = "chat"
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stream: bool = True
    reasoning_enabled: bool = True
    reasoning_adapter: str = ""
    reasoning_effort: str = ""


__all__ = [
    "ProviderModel",
    "ProviderSettings",
    "CustomProviderInfo",
    "ResolvedModel",
]
