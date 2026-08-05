"""LLM 配置的应用服务。"""
from dataclasses import dataclass

from app.core.config import Settings, get_settings
from app.models.schemas import LLMSettings, SettingsResponse, SettingsUpdate, SettingsUpdateResponse
from app.services.llm import reset_providers


@dataclass(frozen=True)
class _ProviderFields:
    api_key: str
    base_url: str
    model: str


_PROVIDER_FIELDS = {
    "anthropic": _ProviderFields(
        api_key="anthropic_api_key",
        base_url="anthropic_base_url",
        model="anthropic_model",
    ),
    "openai": _ProviderFields(
        api_key="openai_api_key",
        base_url="openai_base_url",
        model="openai_model",
    ),
}

_UPDATE_FIELDS = (
    ("llm_api_key", "api_key"),
    ("llm_base_url", "base_url"),
    ("llm_model", "model"),
)


def mask_api_key(api_key: str) -> str:
    """返回用于展示的脱敏 API Key。"""
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "***"
    return f"{api_key[:4]}...{api_key[-4:]}"


def get_llm_config() -> LLMSettings:
    """读取当前 Provider 配置，并隐藏 API Key。"""
    settings = get_settings()
    fields = _provider_fields(settings)
    return LLMSettings(
        provider=settings.llm_provider,
        api_key_masked=mask_api_key(getattr(settings, fields.api_key)),
        base_url=getattr(settings, fields.base_url),
        model=getattr(settings, fields.model),
    )


def get_settings_response() -> SettingsResponse:
    """组装 GET /api/settings 的响应模型，供路由层直接调用。"""
    return SettingsResponse(llm=get_llm_config())


def update_llm_settings(update: SettingsUpdate) -> SettingsUpdateResponse:
    """更新 Provider 配置并重置 Provider 实例。"""
    settings = get_settings()
    if update.llm_provider is not None:
        settings.llm_provider = update.llm_provider
    _apply_updates(settings, update)
    reset_providers()
    return SettingsUpdateResponse(
        message="Settings updated and providers reinitialized",
        settings=SettingsResponse(llm=get_llm_config()),
    )


def _provider_fields(settings: Settings) -> _ProviderFields:
    return _PROVIDER_FIELDS.get(settings.llm_provider, _PROVIDER_FIELDS["openai"])


def _apply_updates(settings: Settings, update: SettingsUpdate) -> None:
    fields = _provider_fields(settings)
    for update_name, field_name in _UPDATE_FIELDS:
        value = getattr(update, update_name)
        if value is not None:
            setattr(settings, getattr(fields, field_name), value)
