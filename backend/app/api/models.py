"""#68 模型列表子路由：``llm_models`` CRUD 五端点。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.schemas import LLMModelCreate, LLMModelResponse, LLMModelUpdate
from app.services import models as models_service
from app.services.models import (
    ModelDefaultConflictError,
    ModelDefaultRequiredError,
    ModelNotFoundError,
)


router = APIRouter()


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
        return await models_service.create_model(db, payload)
    except ModelDefaultRequiredError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
        return await models_service.update_model(db, model_id, payload)
    except ModelNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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
        return await models_service.set_default_model(db, model_id)
    except ModelNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
