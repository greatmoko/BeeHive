"""模型配置到会话请求参数的统一转换，保留会话其余设置。"""
from beehive.system.providers.provider_models import ResolvedModel
from beehive.system.providers.session_settings import ProviderOptions, SessionSettings


def provider_options_for(model: ResolvedModel) -> ProviderOptions:
    return ProviderOptions(
        reasoning_adapter=model.reasoning_adapter,
        reasoning_effort=model.reasoning_effort,
        reasoning_enabled=model.reasoning_enabled,
    )


def apply_model_settings(model: ResolvedModel, settings: SessionSettings) -> None:
    settings.temperature = model.temperature
    settings.top_p = model.top_p
    settings.max_tokens = model.max_tokens
    settings.stream = model.stream
    settings.provider_options = provider_options_for(model)


def settings_for_model(model: ResolvedModel) -> SessionSettings:
    settings = SessionSettings()
    apply_model_settings(model, settings)
    return settings
