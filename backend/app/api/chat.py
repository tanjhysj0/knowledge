"""聊天路由层：仅做 HTTP/SSE 适配、依赖注入和服务调用。

#36：会话上下文隔离 - ``ChatRequest.conversation_id`` 由 Pydantic 强制必填，
``chat_service`` 进一步校验会话存在 (404)；多轮上下文严格按会话 id 过滤，
不再保留全局 ``GET /api/chat/history`` 端点（前端已统一走
``/api/conversations/{id}/messages``）。
"""
from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.schemas import ChatRequest, ChatResponse
from app.services import chat as chat_service
from app.services.conversations import ConversationNotFoundError

router = APIRouter()


@router.post("", response_model=ChatResponse)
async def chat(
    request: Request,
    payload: ChatRequest,
    db: AsyncSession = Depends(get_db),
):
    """Non-streaming RAG-based chat endpoint."""
    try:
        result = await chat_service.ask(
            question=payload.message,
            document_ids=payload.document_ids,
            conversation_id=payload.conversation_id,
            db=db,
            request=request,
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
    """
    return EventSourceResponse(
        chat_service.stream_answer(
            question=payload.message,
            document_ids=payload.document_ids,
            conversation_id=payload.conversation_id,
            db=db,
            request=request,
        )
    )

