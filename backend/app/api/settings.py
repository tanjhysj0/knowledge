"""LLM 配置子路由。"""
from fastapi import APIRouter

from app.models.schemas import SettingsResponse, SettingsUpdateResponse
from app.services.settings import get_settings_response, update_llm_settings


router = APIRouter()


router.add_api_route(
    "/api/settings",
    get_settings_response,
    methods=["GET"],
    response_model=SettingsResponse,
    tags=["settings"],
)


router.add_api_route(
    "/api/settings",
    update_llm_settings,
    methods=["PUT"],
    response_model=SettingsUpdateResponse,
    tags=["settings"],
)