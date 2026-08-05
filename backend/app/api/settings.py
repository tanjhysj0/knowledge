from fastapi import APIRouter
from app.models.schemas import SettingsResponse, SettingsUpdate, LLMSettings, EmbeddingSettings
from app.core.config import get_settings

router = APIRouter()


def mask_api_key(api_key: str) -> str:
    """Mask API key for safe display."""
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "***"
    return f"{api_key[:4]}...{api_key[-4:]}"


def get_llm_config() -> LLMSettings:
    """Get current LLM configuration with masked API key."""
    settings = get_settings()
    if settings.llm_provider == "anthropic":
        return LLMSettings(
            provider=settings.llm_provider,
            api_key_masked=mask_api_key(settings.anthropic_api_key),
            base_url=settings.anthropic_base_url,
            model=settings.anthropic_model,
        )
    return LLMSettings(
        provider=settings.llm_provider,
        api_key_masked=mask_api_key(settings.openai_api_key),
        base_url=settings.openai_base_url,
        model=settings.openai_model,
    )


def get_embedding_config() -> EmbeddingSettings:
    """Get current Embedding configuration with masked API key."""
    settings = get_settings()
    if settings.embedding_provider == "cohere":
        return EmbeddingSettings(
            provider=settings.embedding_provider,
            api_key_masked=mask_api_key(settings.cohere_api_key),
            base_url=settings.cohere_base_url,
            model=settings.cohere_embedding_model,
        )
    return EmbeddingSettings(
        provider=settings.embedding_provider,
        api_key_masked=mask_api_key(settings.openai_api_key),
        base_url=settings.openai_base_url,
        model=settings.embedding_model,
    )


@router.get("", response_model=SettingsResponse)
async def get_settings_api():
    """Get current LLM/Embedding configuration (API keys masked)."""
    return SettingsResponse(
        llm=get_llm_config(),
        embedding=get_embedding_config(),
    )


@router.put("")
async def update_settings(update: SettingsUpdate):
    """Update LLM/Embedding configuration and reinitialize providers."""
    from app.services import llm as llm_service, embedding as embedding_service

    settings = get_settings()

    # Update LLM settings
    if update.llm_provider is not None:
        settings.llm_provider = update.llm_provider
    if update.llm_api_key is not None:
        if settings.llm_provider == "anthropic":
            settings.anthropic_api_key = update.llm_api_key
        else:
            settings.openai_api_key = update.llm_api_key
    if update.llm_base_url is not None:
        if settings.llm_provider == "anthropic":
            settings.anthropic_base_url = update.llm_base_url
        else:
            settings.openai_base_url = update.llm_base_url
    if update.llm_model is not None:
        if settings.llm_provider == "anthropic":
            settings.anthropic_model = update.llm_model
        else:
            settings.openai_model = update.llm_model

    # Update Embedding settings
    if update.embedding_provider is not None:
        settings.embedding_provider = update.embedding_provider
    if update.embedding_api_key is not None:
        if settings.embedding_provider == "cohere":
            settings.cohere_api_key = update.embedding_api_key
        else:
            settings.openai_api_key = update.embedding_api_key
    if update.embedding_base_url is not None:
        if settings.embedding_provider == "cohere":
            settings.cohere_base_url = update.embedding_base_url
        else:
            settings.openai_base_url = update.embedding_base_url
    if update.embedding_model is not None:
        if settings.embedding_provider == "cohere":
            settings.cohere_embedding_model = update.embedding_model
        else:
            settings.embedding_model = update.embedding_model

    # Reinitialize providers
    llm_service.reset_providers()
    embedding_service.reset_providers()

    return {
        "message": "Settings updated and providers reinitialized",
        "settings": {
            "llm": get_llm_config().model_dump(),
            "embedding": get_embedding_config().model_dump(),
        }
    }
