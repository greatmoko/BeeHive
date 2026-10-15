"""厂商配置状态、历史格式兼容和加密持久化。"""
import json
from pathlib import Path
from beehive.system.settings.config import default_data_dir
from beehive.common.io.safe_io import safe_write_json
from beehive.system.providers.provider import get_builtin
from beehive.system.providers.provider_models import ProviderSettings, CustomProviderInfo


class ProviderConfiguration:
    """厂商持久化状态的唯一所有者。"""

    def __init__(self, config_path: str | None = None):
        if config_path:
            self._path = Path(config_path)
        else:
            self._path = default_data_dir() / "providers.json"
        self._providers: dict[str, ProviderSettings] = {}
        self._custom: list[CustomProviderInfo] = []
        self._enabled_ids: list[str] = []  # 「我的厂商」顺序
        self._default_provider: str = ""
        self._default_model_id: str = ""
        self._cleared_builtin_models: bool = False
        self._load()

    def _load(self):
        if not self._path.exists():
            self._providers = {}
            self._custom = []
            self._enabled_ids = []
            self._default_provider = ""
            self._default_model_id = ""
            self._cleared_builtin_models = True
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            raw = data.get("providers") or {}
            self._providers = {
                pid: ProviderSettings.from_dict(cfg)
                for pid, cfg in raw.items()
                if isinstance(cfg, dict)
            }
            self._custom = [
                CustomProviderInfo.from_dict(item)
                for item in (data.get("custom_providers") or [])
                if isinstance(item, dict)
            ]
            enabled = data.get("enabled_providers")
            if isinstance(enabled, list):
                self._enabled_ids = [str(x) for x in enabled]
            else:
                # 兼容旧数据：若已有 providers 配置，视为已添加
                self._enabled_ids = list(self._providers.keys())
                if not self._enabled_ids:
                    self._enabled_ids = [c.id for c in self._custom]
            self._default_provider = str(data.get("default_provider") or "")
            self._default_model_id = str(
                data.get("default_model_id") or data.get("default_model") or ""
            )
            self._cleared_builtin_models = bool(data.get("cleared_builtin_models_v1"))
            if not self._cleared_builtin_models:
                self._clear_builtin_seed_models()
        except (json.JSONDecodeError, OSError, TypeError):
            self._providers = {}
            self._custom = []
            self._enabled_ids = []
            self._default_provider = ""
            self._default_model_id = ""
            self._cleared_builtin_models = True

    def _clear_builtin_seed_models(self) -> None:
        """一次性清除内置厂商里系统预置的模型列表（自定义厂商不动）。"""
        for pid, settings in list(self._providers.items()):
            if get_builtin(pid) is None:
                continue
            settings.models = []
            if self._default_provider == pid:
                self._default_provider = ""
                self._default_model_id = ""
        self._cleared_builtin_models = True
        self.save()

    def save(self):
        data = {
            "enabled_providers": list(self._enabled_ids),
            "providers": {pid: cfg.to_dict() for pid, cfg in self._providers.items()},
            "custom_providers": [c.to_dict() for c in self._custom],
            "default_provider": self._default_provider,
            "default_model_id": self._default_model_id,
            "cleared_builtin_models_v1": True,
        }
        safe_write_json(self._path, data)
