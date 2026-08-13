"""LLM 配置的应用服务（#67 DB 持久化 → #68 模型列表化）。

``llm_models`` 表是 LLM 配置的唯一事实源：启动时由 lifespan 先执行
:func:`migrate_legacy_settings`（旧 ``settings`` 单行一次性迁移），再由
:func:`load_llm_settings_from_db` 把模型列表恢复到内存 ``Settings`` 单例
（provider 构造与 preflight 的读取源）；迁移成功后运行时不再读取旧单行。

``GET/PUT /api/settings`` 兼容旧前端：读写默认模型记录（默认排最前）。
"""
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.llm_model import LLMModel
from app.models.schemas import (
    LLMModelCreate,
    LLMModelUpdate,
    LLMSettings,
    SettingsResponse,
    SettingsUpdate,
    SettingsUpdateResponse,
)
from app.models.setting import AppSetting
from app.services import models as models_service
from app.services.llm import reset_providers


# settings 表固定单行 id（单例行语义，仅迁移期读取）。
_LEGACY_ROW_ID = 1

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


async def migrate_legacy_settings(db: AsyncSession) -> None:
    """#68：旧 ``settings`` 单行 → ``llm_models`` 一次性迁移。

    ``llm_models`` 已有记录或旧行不存在时跳过。按非空字段拆成
    openai / anthropic 各一条记录；原 ``llm_provider`` 对应的记录自动
    设为默认（对应记录不存在时回退第一条）。成功后删除旧行，迁移一次性
    生效，避免后续清空模型列表后旧数据复活；旧表删除留给清理切片。
    """
    if await models_service.list_model_rows(db):
        return
    legacy = await _fetch_legacy_row(db)
    if legacy is None:
        return
    created = []
    for provider, fields in _PROVIDER_FIELDS.items():
        api_key = getattr(legacy, fields.api_key) or ""
        base_url = getattr(legacy, fields.base_url) or ""
        model = getattr(legacy, fields.model) or ""
        if not any((api_key, base_url, model)):
            continue
        created.append(
            LLMModel(
                provider_type=provider,
                api_key=api_key,
                base_url=base_url,
                model_name=model,
                is_default=provider == (legacy.llm_provider or "openai"),
            )
        )
    if created and not any(model.is_default for model in created):
        created[0].is_default = True
    for model in created:
        db.add(model)
    await db.delete(legacy)
    await db.commit()


async def load_llm_settings_from_db(db: AsyncSession) -> None:
    """启动时从 ``llm_models`` 恢复 LLM 配置到内存单例（#68）。

    列表非空 → 各记录写回对应 provider 字段，默认记录（无默认回退第一条）
    决定 ``llm_provider``；列表为空 → 清空内存单例的 LLM 字段（未配置）。
    环境变量/``.env`` 中的 LLM key 不再作为运行时配置来源。
    """
    rows = await models_service.list_model_rows(db)
    if not rows:
        reset_llm_memory()
        return
    _apply_models_to_memory(rows)


def get_llm_config() -> LLMSettings:
    """读取内存单例中当前 Provider 配置，并隐藏 API Key。"""
    settings = get_settings()
    fields = _provider_fields(settings)
    return LLMSettings(
        provider=settings.llm_provider,
        api_key_masked=models_service.mask_api_key(getattr(settings, fields.api_key)),
        base_url=getattr(settings, fields.base_url),
        model=getattr(settings, fields.model),
    )


async def get_settings_response(db: AsyncSession) -> SettingsResponse:
    """组装 ``GET /api/settings`` 响应：读默认模型记录（#68 兼容旧前端）。

    无记录时返回默认空配置（provider=openai、各字段为空）。
    """
    rows = await models_service.list_model_rows(db)
    if not rows:
        return SettingsResponse(
            llm=LLMSettings(provider="openai", api_key_masked="", base_url="", model="")
        )
    row = rows[0]  # 默认排最前
    return SettingsResponse(
        llm=LLMSettings(
            provider=row.provider_type,
            api_key_masked=models_service.mask_api_key(row.api_key),
            base_url=row.base_url,
            model=row.model_name,
        )
    )


async def update_llm_settings(
    db: AsyncSession,
    update: SettingsUpdate,
) -> SettingsUpdateResponse:
    """更新 LLM 配置：写入 ``llm_models`` 后重载内存并重置 Provider（#68）。

    显式给 ``llm_provider`` → 更新/新建该 provider 的记录并设为默认
    （切换生效）；未给 → 更新当前默认记录。先落库、commit 成功后再改
    内存单例，不会与 DB 事实源漂移。
    """
    settings = get_settings()
    rows = await models_service.list_model_rows(db)
    default_row = next((r for r in rows if r.is_default), rows[0] if rows else None)

    if update.llm_provider is not None:
        target_provider = update.llm_provider
        switch_default = True
    else:
        target_provider = (
            default_row.provider_type if default_row else settings.llm_provider
        )
        switch_default = False

    target = next((r for r in rows if r.provider_type == target_provider), None)
    if target is None:
        await models_service.create_model(
            db,
            LLMModelCreate(
                provider_type=target_provider,
                base_url=update.llm_base_url or "",
                model_name=update.llm_model or "",
                api_key=update.llm_api_key or "",
                is_default=True,
            ),
        )
    else:
        await models_service.update_model(
            db,
            target.id,
            LLMModelUpdate(
                base_url=update.llm_base_url,
                model_name=update.llm_model,
                api_key=update.llm_api_key,
            ),
        )
        if switch_default and not target.is_default:
            await models_service.set_default_model(db, target.id)

    await load_llm_settings_from_db(db)
    reset_providers()
    return SettingsUpdateResponse(
        message="Settings updated and providers reinitialized",
        settings=SettingsResponse(llm=get_llm_config()),
    )


async def _fetch_legacy_row(db: AsyncSession) -> AppSetting | None:
    result = await db.execute(
        select(AppSetting).where(AppSetting.id == _LEGACY_ROW_ID)
    )
    return result.scalar_one_or_none()


def _apply_models_to_memory(rows: list[LLMModel]) -> None:
    """模型列表 → 内存单例：各记录写回对应 provider 字段。

    默认记录最后再应用一次，保证多记录同 provider 时默认配置优先生效。
    """
    settings = get_settings()
    reset_llm_memory()
    for row in rows:
        _apply_row_fields(settings, row)
    default = next((row for row in rows if row.is_default), rows[0])
    _apply_row_fields(settings, default)
    settings.llm_provider = default.provider_type


def _apply_row_fields(settings: Settings, row: LLMModel) -> None:
    fields = _PROVIDER_FIELDS.get(row.provider_type)
    if fields is None:
        return
    setattr(settings, fields.api_key, row.api_key)
    setattr(settings, fields.base_url, row.base_url)
    setattr(settings, fields.model, row.model_name)


def reset_llm_memory() -> None:
    """DB 无记录（或启动加载失败）→ 内存单例的 LLM 字段全部清空（未配置）。

    供 :func:`load_llm_settings_from_db` 与 lifespan 容错分支调用（#67）。
    """
    settings = get_settings()
    settings.llm_provider = "openai"
    for field in _LLM_FIELDS[1:]:
        setattr(settings, field, "")


def _provider_fields(settings: Settings) -> _ProviderFields:
    return _PROVIDER_FIELDS.get(settings.llm_provider, _PROVIDER_FIELDS["openai"])
