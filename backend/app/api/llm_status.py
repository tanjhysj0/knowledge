"""#45 聊天页 preflight 用的 LLM 可用性子路由。"""
from fastapi import APIRouter

from app.models.schemas import LLMStatusResponse
from app.services.llm import is_llm_configured
from app.services.runtime_config import get_runtime_model


router = APIRouter()


@router.get(
    "/api/llm/status",
    response_model=LLMStatusResponse,
    tags=["llm"],
)
def get_llm_status() -> LLMStatusResponse:
    """返回当前默认模型是否已就绪。

    #69：与 provider 构造共用运行时默认模型单例（``llm_models`` 默认行
    镜像）；无默认模型时 ``configured=false``。
    """
    runtime = get_runtime_model()
    configured, reason = is_llm_configured()
    return LLMStatusResponse(
        provider=runtime.provider_type,
        configured=configured,
        reason=reason,
    )
