"""v2 聊天路由层：仅做 HTTP/SSE 适配、依赖注入和服务调用。

#77：聊天 API 版本化第二步（端到端）——新增 v2 聊天端点
（``POST /api/v2/chat`` 与 ``POST /api/v2/chat/stream``）。v2 与 v1 共用
同一 ``chat_service`` 与公用 RAG 模块（无重复实现），仅在接入层固定传入
子集检索策略白名单（:data:`CHAT_STRATEGIES`）：并行检索一步与证据循环
补充检索都只调用白名单内策略，最终证据包 ``hit.strategy`` 集合 ⊆ 白名单。

对外契约与 v1 完全一致：请求体（``ChatRequest``，不含 ``strategies`` 字段）、
SSE 事件序列（thinking / message / evidence / done / error）、404（会话
不存在）、503（LLM 未配置）语义与消息落库逻辑均相同。

#36：会话上下文隔离 - ``ChatRequest.conversation_id`` 由 Pydantic 强制必填，
``chat_service`` 进一步校验会话存在 (404)；多轮上下文严格按会话 id 过滤。

#45：LLM 未配置时两个端点都立即拒绝 - 非流式返回 503 JSON，
流式先产 ``event: error``（带 ``reason``）再 ``event: done``。
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

router = APIRouter()

# #77：v2 接入层固定传入的子集检索策略白名单（业务子集：dense + bm25）；
# 并行检索一步与证据循环补充检索均受其约束，证据包 hit.strategy ⊆ 白名单。
CHAT_STRATEGIES = ["dense", "bm25"]


@router.post("", response_model=ChatResponse)
async def chat(
    request: Request,
    payload: ChatRequest,
    db: AsyncSession = Depends(get_db),
):
    """Non-streaming RAG-based chat endpoint (v2).

    #77：接入层固定传入子集检索策略白名单（:data:`CHAT_STRATEGIES`）。
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
    """Streaming RAG-based chat with multi-turn context (v2).

    ``conversation_id`` 不存在时 ``chat_service.stream_answer`` 会发出
    单条 ``event: error`` SSE，事件 ``data.error`` 携带会话不存在的异常
    消息（前端据此提示用户刷新页面）。

    #77：接入层固定传入子集检索策略白名单（:data:`CHAT_STRATEGIES`）。
    #45：preflight 不通过时立即产 ``error`` + ``done`` SSE，不调 LLM / 不写库。
    # mock 只替换 provider 选择（get_llm_provider），不绕过配置守卫。
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
