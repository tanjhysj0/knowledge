"""统一 API 路由声明。"""
from fastapi import APIRouter

from app.api import chat, documents
from app.models.schemas import SettingsResponse, SettingsUpdate, SettingsUpdateResponse
from app.services.health import get_health_status
from app.services.settings import get_llm_config, update_llm_settings


router = APIRouter()


@router.get("/health")
async def health_check():
    """返回服务健康状态。"""
    return get_health_status()


router.include_router(documents.router, prefix="/api/documents", tags=["documents"])
router.include_router(chat.router, prefix="/api/chat", tags=["chat"])


@router.get("/api/settings", response_model=SettingsResponse, tags=["settings"])
async def get_settings_api():
    """获取脱敏后的当前 LLM 配置。"""
    return SettingsResponse(llm=get_llm_config())


@router.put(
    "/api/settings",
    response_model=SettingsUpdateResponse,
    tags=["settings"],
)
async def update_settings_api(update: SettingsUpdate):
    """更新 LLM 配置并重置 Provider。"""
    return update_llm_settings(update)
