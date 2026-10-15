"""可用模型解析与默认模型管理。"""
from beehive.system.providers.provider import infer_family
from beehive.system.providers.provider_configuration import ProviderConfiguration
from beehive.system.providers.provider_models import ProviderModel, ResolvedModel


class ModelCatalog:
    """读取厂商服务，操作唯一配置状态中的模型引用。"""

    def __init__(self, data: ProviderConfiguration, providers_service):
        self._data = data
        self._providers_service = providers_service

    def get_default_ref(self) -> tuple[str, str]:
        """返回 (provider_id, model_id)，未设置则为 ("", "")。"""
        return self._data._default_provider, self._data._default_model_id

    def is_default_model(self, provider_id: str, model_id: str) -> bool:
        return (
            bool(provider_id)
            and bool(model_id)
            and self._data._default_provider == provider_id
            and self._data._default_model_id == model_id
        )

    def set_default_model(self, provider_id: str, model_id: str):
        """设定新建对话使用的默认模型（需为已启用模型）。"""
        if not provider_id or not model_id:
            raise ValueError("厂商或模型无效")
        settings = self._providers_service.get_settings(provider_id)
        matched = next(
            (m for m in settings.models if m.model_id == model_id),
            None,
        )
        if not matched:
            raise ValueError("模型不存在")
        if not matched.enabled:
            matched.enabled = True
            self._data._providers[provider_id] = settings
        self._data._default_provider = provider_id
        self._data._default_model_id = model_id
        self._data.save()

    def clear_default_model(self):
        self._data._default_provider = ""
        self._data._default_model_id = ""
        self._data.save()

    def resolve_default(self) -> ResolvedModel | None:
        """解析当前默认模型；若已失效（未启用/无 Host）则返回 None。"""
        if not self._data._default_provider or not self._data._default_model_id:
            return None
        for m in self.list_selectable_models():
            if (
                m.provider_id == self._data._default_provider
                and m.model_id == self._data._default_model_id
            ):
                return m
        return None

    def default_display_label(self) -> str:
        """用于 UI 展示的默认模型文案。"""
        resolved = self.resolve_default()
        if resolved:
            return f"{resolved.provider_name} / {resolved.model_id}"
        if self._data._default_provider and self._data._default_model_id:
            name = self._providers_service.get_display_name(self._data._default_provider)
            return f"{name} / {self._data._default_model_id}（暂不可用）"
        return "未设置"

    def enabled_models(self, provider_id: str) -> list[ProviderModel]:
        return [m for m in self._providers_service.get_settings(provider_id).models if m.enabled and m.model_id]

    def list_selectable_models(self) -> list[ResolvedModel]:
        """仅「我的厂商」中已勾选启用的模型。"""
        result: list[ResolvedModel] = []
        for pid, name, _icon, family in self._providers_service.list_my_providers():
            settings = self._providers_service.get_settings(pid)
            host = (settings.api_host or "").strip()
            if not host:
                continue
            for m in settings.models:
                if not m.enabled or not m.model_id:
                    continue
                result.append(ResolvedModel(
                    provider_id=pid,
                    provider_name=name,
                    model_id=m.model_id,
                    api_host=host,
                    api_key=settings.api_key,
                    family=family or infer_family(pid, host),
                    context_window=m.context_window,
                    api_protocol=m.api_protocol,
                    temperature=m.temperature,
                    top_p=m.top_p,
                    max_tokens=m.max_tokens,
                    stream=m.stream,
                    reasoning_enabled=m.reasoning_enabled,
                    reasoning_adapter=m.reasoning_adapter,
                    reasoning_effort=m.reasoning_effort,
                ))
        return result

    def resolve(self, provider_id: str, model_id: str) -> ResolvedModel | None:
        if not provider_id or not model_id:
            return None
        if provider_id not in self._data._enabled_ids and not self._providers_service.is_custom(provider_id):
            # 厂商已从「我的厂商」移除，不再用缓存密钥继续调用
            return None
        settings = self._providers_service.get_settings(provider_id)
        host = (settings.api_host or "").strip()
        if not host:
            return None
        name = self._providers_service.get_display_name(provider_id)
        family = self._providers_service.get_family(provider_id)
        ctx = 0
        api_protocol = "chat"
        model_options = ProviderModel(model_id=model_id)
        for m in settings.models:
            if m.model_id == model_id:
                ctx = m.context_window
                api_protocol = m.api_protocol
                model_options = m
                break
        return ResolvedModel(
            provider_id=provider_id,
            provider_name=name,
            model_id=model_id,
            api_host=host,
            api_key=settings.api_key,
            family=family,
            context_window=ctx,
            api_protocol=api_protocol,
            temperature=model_options.temperature,
            top_p=model_options.top_p,
            max_tokens=model_options.max_tokens,
            stream=model_options.stream,
            reasoning_enabled=model_options.reasoning_enabled,
            reasoning_adapter=model_options.reasoning_adapter,
            reasoning_effort=model_options.reasoning_effort,
        )

    def has_any_model(self) -> bool:
        return bool(self.list_selectable_models())

    def first_resolved(self) -> ResolvedModel | None:
        """新建对话优先用默认模型；否则取列表第一个可用模型。"""
        default = self.resolve_default()
        if default:
            return default
        models = self.list_selectable_models()
        return models[0] if models else None
