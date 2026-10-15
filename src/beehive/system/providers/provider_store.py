"""厂商列表与配置编辑服务，协调持久化和模型目录。"""
from beehive.system.providers.provider import BUILTIN_PROVIDERS, get_builtin, infer_family
from beehive.system.providers.provider_models import ProviderSettings, ProviderModel, CustomProviderInfo, ResolvedModel
from beehive.system.providers.provider_configuration import ProviderConfiguration
from beehive.system.providers.model_catalog import ModelCatalog


class ProviderStore:
    """保留业务使用的厂商服务入口，不复制持久化状态。"""

    def __init__(self, config_path: str | None = None):
        self._data = ProviderConfiguration(config_path)
        self._models = ModelCatalog(self._data, self)

    def save(self):
        self._data.save()

    def list_my_providers(self) -> list[tuple[str, str, str, str]]:
        """我的厂商列表 [(id, name, icon, family), ...]。"""
        result: list[tuple[str, str, str, str]] = []
        seen: set[str] = set()
        for pid in self._data._enabled_ids:
            if pid in seen:
                continue
            info = self._provider_meta(pid)
            if info:
                result.append(info)
                seen.add(pid)
        return result

    def list_addable_builtins(self) -> list[tuple[str, str, str, str]]:
        """尚未加入「我的厂商」的内置厂商。"""
        enabled = set(self._data._enabled_ids)
        return [
            (p.id, p.name, p.icon, p.family)
            for p in BUILTIN_PROVIDERS
            if p.id not in enabled
        ]

    def _provider_meta(self, provider_id: str) -> tuple[str, str, str, str] | None:
        b = get_builtin(provider_id)
        if b:
            return (b.id, b.name, b.icon, b.family)
        for c in self._data._custom:
            if c.id == provider_id:
                return (c.id, c.name, c.icon, c.family)
        return None

    def get_display_name(self, provider_id: str) -> str:
        meta = self._provider_meta(provider_id)
        return meta[1] if meta else provider_id

    def get_family(self, provider_id: str) -> str:
        meta = self._provider_meta(provider_id)
        if meta:
            return meta[3]
        settings = self.get_settings(provider_id)
        return infer_family(provider_id, settings.api_host)

    def is_custom(self, provider_id: str) -> bool:
        return provider_id.startswith("custom-") or any(c.id == provider_id for c in self._data._custom)

    def get_settings(self, provider_id: str) -> ProviderSettings:
        if provider_id in self._data._providers:
            return self._data._providers[provider_id]
        return self._default_settings(provider_id)

    def _default_settings(self, provider_id: str) -> ProviderSettings:
        b = get_builtin(provider_id)
        if b:
            # 内置厂商只带默认 Host，模型需用户拉取或手动添加
            return ProviderSettings(api_host=b.api_host, models=[])
        return ProviderSettings()

    def update_settings(self, provider_id: str, settings: ProviderSettings):
        self._data._providers[provider_id] = settings
        if provider_id not in self._data._enabled_ids:
            self._data._enabled_ids.append(provider_id)
        # 若默认模型被取消勾选，自动清除
        if (
            self._data._default_provider == provider_id
            and self._data._default_model_id
            and not any(
                m.enabled and m.model_id == self._data._default_model_id
                for m in settings.models
            )
        ):
            self._data._default_provider = ""
            self._data._default_model_id = ""
        self.save()

    def add_builtin_provider(self, provider_id: str) -> bool:
        """将内置厂商加入我的列表。已存在则返回 False。"""
        if get_builtin(provider_id) is None:
            return False
        if provider_id in self._data._enabled_ids:
            return False
        if provider_id not in self._data._providers:
            self._data._providers[provider_id] = self._default_settings(provider_id)
        self._data._enabled_ids.append(provider_id)
        self.save()
        return True

    def add_custom_provider(
        self,
        name: str,
        api_host: str = "",
        api_key: str = "",
    ) -> CustomProviderInfo:
        """添加自定义本地 / 私有 OpenAI 兼容厂商（需填写厂商名称）。"""
        clean = name.strip()
        if not clean:
            raise ValueError("请填写厂商名称")
        info = CustomProviderInfo(name=clean)
        self._data._custom.append(info)
        self._data._providers[info.id] = ProviderSettings(
            api_key=api_key.strip(),
            api_host=api_host.strip(),
            models=[],
        )
        self._data._enabled_ids.append(info.id)
        self.save()
        return info

    def remove_from_my_list(self, provider_id: str):
        """从我的厂商中移除；自定义厂商同时删除配置。"""
        self._data._enabled_ids = [x for x in self._data._enabled_ids if x != provider_id]
        if self.is_custom(provider_id):
            self._data._custom = [c for c in self._data._custom if c.id != provider_id]
            self._data._providers.pop(provider_id, None)
        if self._data._default_provider == provider_id:
            self._data._default_provider = ""
            self._data._default_model_id = ""
        self.save()

    def reset_provider(self, provider_id: str, *, keep_api_key: bool = True):
        key = ""
        if keep_api_key and provider_id in self._data._providers:
            key = self._data._providers[provider_id].api_key
        settings = self._default_settings(provider_id)
        settings.api_key = key
        self._data._providers[provider_id] = settings
        if self._data._default_provider == provider_id:
            self._data._default_provider = ""
            self._data._default_model_id = ""
        self.save()

    def get_default_ref(self) -> tuple[str, str]:
        return self._models.get_default_ref()

    def is_default_model(self, provider_id: str, model_id: str) -> bool:
        return self._models.is_default_model(provider_id, model_id)

    def set_default_model(self, provider_id: str, model_id: str):
        return self._models.set_default_model(provider_id, model_id)

    def clear_default_model(self):
        return self._models.clear_default_model()

    def resolve_default(self) -> ResolvedModel | None:
        return self._models.resolve_default()

    def default_display_label(self) -> str:
        return self._models.default_display_label()

    def enabled_models(self, provider_id: str) -> list[ProviderModel]:
        return self._models.enabled_models(provider_id)

    def list_selectable_models(self) -> list[ResolvedModel]:
        return self._models.list_selectable_models()

    def resolve(self, provider_id: str, model_id: str) -> ResolvedModel | None:
        return self._models.resolve(provider_id, model_id)

    def has_any_model(self) -> bool:
        return self._models.has_any_model()

    def first_resolved(self) -> ResolvedModel | None:
        return self._models.first_resolved()
