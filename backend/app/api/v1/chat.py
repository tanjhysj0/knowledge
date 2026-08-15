"""v1 聊天路由层：仅做 HTTP/SSE 适配、依赖注入和服务调用。

#76：聊天 API 版本化第一步——端点从 ``/api/chat`` 迁移到 ``/api/v1/chat``；
v1 路由在接入层显式传入全量六路检索策略白名单（:data:`CHAT_STRATEGIES`），
行为与迁移前完全一致（答案 / sources / SSE 事件序列不变）。

#79：v1 白名单动态化——改为装配层推导的"当前启用全集"（settings 开关开启
的已登记检索器，忠实还原迁移前"默认生效策略集合"语义）；新增检索器 + 打开
开关即自动进入 v1。

#36：会话上下文隔离 - ``ChatRequest.conversation_id`` 由 Pydantic 强制必填，
``chat_service`` 进一步校验会话存在 (404)；多轮上下文严格按会话 id 过滤，
不再保留全局 ``GET /api/chat/history`` 端点（前端已统一走
``/api/conversations/{id}/messages``）。

#45：LLM 未配置时两个端点都立即拒绝 - 非流式返回 503 JSON，
流式先产 ``event: error``（带 ``reason``）再 ``event: done``。
#77：503 拒绝形状抽到 :mod:`app.api.chat_common`，v1 / v2 共用。
"""
from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.chat_common import llm_unavailable_events, llm_unavailable_json
from app.core.database import get_db
from app.models.schemas import ChatRequest, ChatResponse
from app.services import chat as chat_service
from app.services.conversations import ConversationNotFoundError
from app.services.llm import is_llm_configured
from app.services.retrieval.assembly import enabled_strategy_names

router = APIRouter()

# #79：v1 接入层的"当前启用全集"白名单——由装配层按 settings 开关推导
# （默认全开 = 六路全量，与迁移前默认生效策略集合一致）；新增检索器 +
# 打开开关即自动进入 v1（v2 保持显式业务子集，见 :mod:`app.api.v2.chat`）。
CHAT_STRATEGIES = enabled_strategy_names()


@router.post("", response_model=ChatResponse)
async def chat(
    request: Request,
    payload: ChatRequest,
    db: AsyncSession = Depends(get_db),
):
    """Non-streaming RAG-based chat endpoint.

    #76/#79：接入层传入动态推导的"当前启用全集"白名单（:data:`CHAT_STRATEGIES`）。
    #45：preflight 不通过时直接返回 503，不调 LLM / 不写库。
    """
    configured, reason = is_llm_configured()
    if not configured:
        return llm_unavailable_json(reason)

    try:
        result = await chat_service.ask(
            question=payload.message,
            document_ids=payload.document_ids,
            conversation_id=payload.conversation_id,
            db=db,
            request=request,
            strategies=CHAT_STRATEGIES,
        )
    except ConversationNotFoundError as exc:
        # #36：会话不存在 → 404（之前会写入孤儿消息）
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ChatResponse(message=result["answer"], sources=result["sources"])


@router.post("/stream")
async def chat_stream(
    request: Request,
    payload: ChatRequest,
    db: AsyncSession = Depends(get_db),
):
    """Streaming RAG-based chat with multi-turn context.

    ``conversation_id`` 不存在时 ``chat_service.stream_answer`` 会发出
    单条 ``event: error`` SSE，事件 ``data.error`` 携带 404 信息，前端
    据此提示用户刷新页面。

    #76/#79：接入层传入动态推导的"当前启用全集"白名单（:data:`CHAT_STRATEGIES`）。
    #45：preflight 不通过时立即产 ``error`` + ``done`` SSE，不调 LLM / 不写库。
    # mock 只替换 provider 选择（get_llm_provider），不绕过配置守卫；
    # E2E 聊天 spec 由 modelsGuard 在 setup 阶段写入 dummy key。
    """
    configured, reason = is_llm_configured()
    if not configured:
        return EventSourceResponse(llm_unavailable_events(reason))

    return EventSourceResponse(
        chat_service.stream_answer(
            question=payload.message,
            document_ids=payload.document_ids,
            conversation_id=payload.conversation_id,
            db=db,
            request=request,
            strategies=CHAT_STRATEGIES,
        )
    )
