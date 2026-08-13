"""模型列表应用服务（#68）：``llm_models`` 表的纯数据层 CRUD。

一个模型一条记录（provider_type / base_url / model_name / api_key /
is_default）；「有且只有一个默认」由数据库 partial unique index 保证。
本模块不触碰内存单例与 Provider 构造（运行时管线切换在后续切片），
只提供模型记录的读写与领域错误。
"""
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_model import LLMModel
from app.models.schemas import LLMModelCreate, LLMModelResponse, LLMModelUpdate


class ModelServiceError(Exception):
    """模型应用服务异常基类。"""


class ModelNotFoundError(ModelServiceError):
    """请求的模型记录不存在。"""


class ModelDefaultConflictError(ModelServiceError):
    """默认模型删除冲突：列表中仍有其它记录。"""


class ModelDefaultRequiredError(ModelServiceError):
    """列表为空时新增必须设为默认。"""


def mask_api_key(api_key: str) -> str:
    """返回用于展示的脱敏 API Key。"""
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "***"
    return f"{api_key[:4]}...{api_key[-4:]}"


async def list_model_rows(db: AsyncSession) -> list[LLMModel]:
    """全量模型记录：默认排最前，其余按 id 升序。"""
    result = await db.execute(
        select(LLMModel).order_by(LLMModel.is_default.desc(), LLMModel.id.asc())
    )
    return list(result.scalars().all())


async def list_models(db: AsyncSession) -> list[LLMModelResponse]:
    """``GET /api/models``：列表，api_key 脱敏返回。"""
    return [to_response(row) for row in await list_model_rows(db)]


async def create_model(
    db: AsyncSession,
    payload: LLMModelCreate,
) -> LLMModelResponse:
    """``POST /api/models``。

    列表为空时 ``is_default`` 必须为 true（拒绝 400）；``is_default=true``
    时先原子降级既有默认（同事务），再插入新默认，避免触发唯一索引冲突。
    """
    rows = await list_model_rows(db)
    if not rows and not payload.is_default:
        raise ModelDefaultRequiredError("列表为空时第一个模型必须设为默认")
    if payload.is_default:
        await _lock_model_rows(db)
        await _demote_defaults(db)
    row = LLMModel(
        provider_type=payload.provider_type,
        base_url=payload.base_url,
        model_name=payload.model_name,
        api_key=payload.api_key,
        is_default=payload.is_default,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return to_response(row)


async def update_model(
    db: AsyncSession,
    model_id: int,
    payload: LLMModelUpdate,
) -> LLMModelResponse:
    """``PUT /api/models/{id}``：部分编辑；``api_key`` 留空 = 保持原值。"""
    row = await _get_model_row(db, model_id)
    if row is None:
        raise ModelNotFoundError(f"Model {model_id} not found")
    if payload.provider_type is not None:
        row.provider_type = payload.provider_type
    if payload.base_url is not None:
        row.base_url = payload.base_url
    if payload.model_name is not None:
        row.model_name = payload.model_name
    if payload.api_key:
        row.api_key = payload.api_key
    await db.commit()
    return to_response(row)


async def delete_model(db: AsyncSession, model_id: int) -> None:
    """``DELETE /api/models/{id}``。

    被删记录是默认且列表中仍有其它记录 → 拒绝；仅剩一条默认时允许
    删除（回到未配置状态）。
    """
    row = await _get_model_row(db, model_id)
    if row is None:
        raise ModelNotFoundError(f"Model {model_id} not found")
    others = [r for r in await list_model_rows(db) if r.id != model_id]
    if row.is_default and others:
        raise ModelDefaultConflictError("默认模型不可删除：请先将其他模型设为默认")
    await db.delete(row)
    await db.commit()


async def set_default_model(db: AsyncSession, model_id: int) -> LLMModelResponse:
    """``PUT /api/models/{id}/default``：设默认。

    先全量降级再提升目标（同事务、单次 commit 原子生效）。PostgreSQL
    对 unique index 逐行即时校验，单条 CASE 更新会在旧默认行尚未变
    false 时触发唯一冲突，故必须两步走。降级前对全表行加 ``FOR UPDATE``
    锁，串行化并发写默认标志的事务，避免两个事务同时通过降级后各自
    提升导致唯一索引冲突。
    """
    row = await _get_model_row(db, model_id)
    if row is None:
        raise ModelNotFoundError(f"Model {model_id} not found")
    await _lock_model_rows(db)
    await _demote_defaults(db)
    await db.execute(
        update(LLMModel)
        .where(LLMModel.id == model_id)
        .values(is_default=True)
    )
    await db.commit()
    refreshed = await _get_model_row(db, model_id)
    return to_response(refreshed)


async def _get_model_row(db: AsyncSession, model_id: int) -> LLMModel | None:
    result = await db.execute(select(LLMModel).where(LLMModel.id == model_id))
    return result.scalar_one_or_none()


async def _lock_model_rows(db: AsyncSession) -> None:
    """对全表模型行加 ``FOR UPDATE`` 锁，串行化并发修改默认标志的事务。

    在 READ COMMITTED 下，两个并发事务各自降级再提升时，后者可能看不到
    前者刚提升的新默认行，导致 partial unique index 冲突。锁住全部既有行
    使后者在 demote 阶段阻塞至前者提交，从而看到最新默认状态。
    """
    await db.execute(select(LLMModel.id).with_for_update())


async def _demote_defaults(db: AsyncSession) -> None:
    """把现有默认记录降级为普通记录（配合「新增即默认」使用）。"""
    await db.execute(
        update(LLMModel)
        .where(LLMModel.is_default.is_(True))
        .values(is_default=False)
    )


def to_response(row: LLMModel) -> LLMModelResponse:
    return LLMModelResponse(
        id=row.id,
        provider_type=row.provider_type,
        base_url=row.base_url,
        model_name=row.model_name,
        api_key_masked=mask_api_key(row.api_key),
        is_default=row.is_default,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
