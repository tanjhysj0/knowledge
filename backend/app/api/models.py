"""#68/#69 模型列表子路由：``llm_models`` CRUD 五端点 + 模型列表拉取代理。

#69 起全部写路径（新增 / 编辑 / 删除 / 设默认）提交成功后都会
:func:`_sync_runtime`：把默认行镜像进运行时单例并重建 provider 实例，
保证「修改默认模型后下一次对话生效」而无需重启进程。
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from app.core.database import get_db
from app.models.schemas import (
    LLMModelCreate,
    LLMModelResponse,
    LLMModelUpdate,
    ModelListFetchRequest,
    ModelListResponse,
)
from app.services import models as models_service
from app.services.llm import reset_providers
from app.services.models import (
    ModelDefaultConflictError,
    ModelDefaultRequiredError,
    ModelNotFoundError,
)


router = APIRouter()


async def _sync_runtime(db: AsyncSession) -> None:
    """写路径收尾：默认行 → 运行时单例，并重建 provider 实例。"""
    await models_service.sync_runtime_model_from_db(db)
    reset_providers()


@router.get(
    "/api/models",
    response_model=list[LLMModelResponse],
    tags=["models"],
)
async def list_models(
    db: AsyncSession = Depends(get_db),
) -> list[LLMModelResponse]:
    """模型列表：默认排最前，api_key 脱敏返回。"""
    return await models_service.list_models(db)


@router.post(
    "/api/models/fetch",
    response_model=ModelListResponse,
    tags=["models"],
)
async def fetch_provider_models(
    payload: ModelListFetchRequest,
) -> ModelListResponse:
    """#69：后端代理拉取 provider 模型列表（OpenAI 兼容 / Anthropic）。

    api_key 仅透传给上游 provider，不落库、不暴露给前端。
    """
    try:
        models = await models_service.fetch_provider_models(payload)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"拉取模型列表失败：{exc}"
        ) from exc
    return ModelListResponse(models=models)


@router.post(
    "/api/models",
    response_model=LLMModelResponse,
    status_code=201,
    tags=["models"],
)
async def create_model(
    payload: LLMModelCreate,
    db: AsyncSession = Depends(get_db),
) -> LLMModelResponse:
    """新增模型：列表为空时 is_default 必须为 true；默认新增自动降级旧默认。"""
    try:
        response = await models_service.create_model(db, payload)
    except ModelDefaultRequiredError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _sync_runtime(db)
    return response


@router.put(
    "/api/models/{model_id}",
    response_model=LLMModelResponse,
    tags=["models"],
)
async def update_model(
    model_id: int,
    payload: LLMModelUpdate,
    db: AsyncSession = Depends(get_db),
) -> LLMModelResponse:
    """编辑模型：api_key 留空 = 保持原值。"""
    try:
        response = await models_service.update_model(db, model_id, payload)
    except ModelNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _sync_runtime(db)
    return response


@router.delete(
    "/api/models/{model_id}",
    status_code=204,
    tags=["models"],
)
async def delete_model(
    model_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """删除模型：默认且列表仍有其它记录 → 400；仅剩一条默认允许删除。"""
    try:
        await models_service.delete_model(db, model_id)
    except ModelNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ModelDefaultConflictError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _sync_runtime(db)


@router.put(
    "/api/models/{model_id}/default",
    response_model=LLMModelResponse,
    tags=["models"],
)
async def set_default_model(
    model_id: int,
    db: AsyncSession = Depends(get_db),
) -> LLMModelResponse:
    """设默认：原子地把其它记录 is_default 置 false。"""
    try:
        response = await models_service.set_default_model(db, model_id)
    except ModelNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _sync_runtime(db)
    return response
