"""LLM 配置子路由（#67：DB 持久化）。"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.schemas import SettingsResponse, SettingsUpdate, SettingsUpdateResponse
from app.services import settings as settings_service


router = APIRouter()


@router.get(
    "/api/settings",
    response_model=SettingsResponse,
    tags=["settings"],
)
async def get_settings(db: AsyncSession = Depends(get_db)) -> SettingsResponse:
    """从 DB 读取当前 LLM 配置（API Key 脱敏后返回）。"""
    return await settings_service.get_settings_response(db)


@router.put(
    "/api/settings",
    response_model=SettingsUpdateResponse,
    tags=["settings"],
)
async def update_settings(
    update: SettingsUpdate,
    db: AsyncSession = Depends(get_db),
) -> SettingsUpdateResponse:
    """更新 LLM 配置并持久化到 DB（#67），随后重置 Provider 实例。"""
    return await settings_service.update_llm_settings(db=db, update=update)
