"""LLM 配置的应用服务（#67：DB 持久化）。

``settings`` 单行表是 LLM 配置的唯一事实源：启动时由 lifespan 调用
:func:`load_llm_settings_from_db` 恢复到内存 ``Settings`` 单例（provider
构造与 preflight 的读取源），``PUT /api/settings`` 双写 DB 与内存并
:func:`reset_providers`；``GET /api/settings`` 直接读 DB。
"""
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.schemas import LLMSettings, SettingsResponse, SettingsUpdate, SettingsUpdateResponse
from app.models.setting import AppSetting
from app.services.llm import reset_providers


# settings 表固定单行 id（单例行语义）。
_SETTING_ROW_ID = 1

# 与 LLM 配置相关的内存单例字段（DB 行镜像）。
_LLM_FIELDS = (
    "llm_provider",
    "openai_api_key",
    "openai_base_url",
    "openai_model",
    "anthropic_api_key",
    "anthropic_base_url",
    "anthropic_model",
)


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


async def load_llm_settings_from_db(db: AsyncSession) -> None:
    """启动时从 DB 恢复 LLM 配置到内存单例（#67）。

    DB 有行 → 覆盖内存单例；无行 → 清空内存单例的 LLM 字段（视为未配置）。
    环境变量/``.env`` 中的 LLM key 不再作为运行时配置来源。
    """
    row = await _fetch_row(db)
    if row is None:
        reset_llm_memory()
        return
    _apply_row_to_memory(row)


def get_llm_config() -> LLMSettings:
    """读取内存单例中当前 Provider 配置，并隐藏 API Key。"""
    settings = get_settings()
    fields = _provider_fields(settings)
    return LLMSettings(
        provider=settings.llm_provider,
        api_key_masked=mask_api_key(getattr(settings, fields.api_key)),
        base_url=getattr(settings, fields.base_url),
        model=getattr(settings, fields.model),
    )


async def get_settings_response(db: AsyncSession) -> SettingsResponse:
    """组装 ``GET /api/settings`` 响应：直接读 DB 行（#67）。

    DB 无行时返回默认空配置（provider=openai、各字段为空）。
    """
    row = await _fetch_row(db)
    if row is None:
        return SettingsResponse(
            llm=LLMSettings(provider="openai", api_key_masked="", base_url="", model="")
        )
    return SettingsResponse(llm=_llm_settings_from_row(row))


async def update_llm_settings(
    db: AsyncSession,
    update: SettingsUpdate,
) -> SettingsUpdateResponse:
    """更新 Provider 配置：先落库、成功后再写内存并重置 Provider（#67）。

    先算目标值 → upsert DB → 全部成功后才改内存单例：
    commit 失败时内存保持原值，不会与 DB 事实源漂移。
    """
    settings = get_settings()
    provider = (
        update.llm_provider
        if update.llm_provider is not None
        else settings.llm_provider
    )
    fields = _provider_fields_for(provider)
    pending = {field: getattr(settings, field) for field in _LLM_FIELDS}
    pending["llm_provider"] = provider
    for update_name, field_name in _UPDATE_FIELDS:
        value = getattr(update, update_name)
        if value is not None:
            pending[getattr(fields, field_name)] = value
    await _upsert_row(db, pending)
    for field, value in pending.items():
        setattr(settings, field, value)
    reset_providers()
    return SettingsUpdateResponse(
        message="Settings updated and providers reinitialized",
        settings=SettingsResponse(llm=get_llm_config()),
    )


async def _fetch_row(db: AsyncSession) -> AppSetting | None:
    result = await db.execute(
        select(AppSetting).where(AppSetting.id == _SETTING_ROW_ID)
    )
    return result.scalar_one_or_none()


async def _upsert_row(db: AsyncSession, values: dict[str, str]) -> None:
    """单行 upsert（#67）：``INSERT ... ON CONFLICT (id) DO UPDATE`` 原子落库。

    一条语句消除 fetch→insert 的 TOCTOU 竞态；``updated_at`` 同步刷新。
    """
    stmt = pg_insert(AppSetting).values(id=_SETTING_ROW_ID, **values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[AppSetting.id],
        set_={**values, "updated_at": func.now()},
    )
    await db.execute(stmt)
    await db.commit()


def _apply_row_to_memory(row: AppSetting) -> None:
    settings = get_settings()
    for field in _LLM_FIELDS:
        setattr(settings, field, getattr(row, field))


def reset_llm_memory() -> None:
    """DB 无记录（或启动加载失败）→ 内存单例的 LLM 字段全部清空（未配置）。

    供 :func:`load_llm_settings_from_db` 与 lifespan 容错分支调用（#67）。
    """
    settings = get_settings()
    settings.llm_provider = "openai"
    for field in _LLM_FIELDS[1:]:
        setattr(settings, field, "")


def _llm_settings_from_row(row: AppSetting) -> LLMSettings:
    provider = row.llm_provider or "openai"
    fields = _provider_fields_for(provider)
    return LLMSettings(
        provider=provider,
        api_key_masked=mask_api_key(getattr(row, fields.api_key)),
        base_url=getattr(row, fields.base_url),
        model=getattr(row, fields.model),
    )


def _provider_fields(settings: Settings) -> _ProviderFields:
    return _provider_fields_for(settings.llm_provider)


def _provider_fields_for(provider: str) -> _ProviderFields:
    return _PROVIDER_FIELDS.get(provider, _PROVIDER_FIELDS["openai"])
