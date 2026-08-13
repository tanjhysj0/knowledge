"""#45 聊天页 preflight 用的 LLM 可用性子路由。"""
from fastapi import APIRouter

from app.core.config import get_settings
from app.models.schemas import LLMStatusResponse
from app.services.llm import is_llm_configured


router = APIRouter()


@router.get(
    "/api/llm/status",
    response_model=LLMStatusResponse,
    tags=["llm"],
)
def get_llm_status() -> LLMStatusResponse:
    """返回当前 LLM Provider 是否已就绪。

    与 ``GET /api/settings`` 共享同一事实源：``is_llm_configured()`` 只读
    ``settings.llm_provider`` + 对应 provider 的 ``api_key`` / ``model``。
    """
    settings = get_settings()
    configured, reason = is_llm_configured()
    return LLMStatusResponse(
        provider=settings.llm_provider,
        configured=configured,
        reason=reason,
    )
