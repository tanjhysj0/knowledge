"""聊天路由层：仅做 HTTP/SSE 适配、依赖注入和服务调用。"""
from typing import List

from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.schemas import ChatMessageResponse, ChatRequest, ChatResponse
from app.services import chat as chat_service

router = APIRouter()


@router.post("", response_model=ChatResponse)
async def chat(
    request: Request,
    payload: ChatRequest,
    db: AsyncSession = Depends(get_db),
):
    """Non-streaming RAG-based chat endpoint."""
    result = await chat_service.ask(
        question=payload.message,
        document_ids=payload.document_ids,
        db=db,
        request=request,
    )
    return ChatResponse(message=result["answer"], sources=result["sources"])


@router.post("/stream")
async def chat_stream(
    request: Request,
    payload: ChatRequest,
    db: AsyncSession = Depends(get_db),
):
    """Streaming RAG-based chat with multi-turn context."""
    return EventSourceResponse(
        chat_service.stream_answer(
            question=payload.message,
            document_ids=payload.document_ids,
            db=db,
            request=request,
        )
    )


@router.get("/history", response_model=List[ChatMessageResponse])
async def get_chat_history(db: AsyncSession = Depends(get_db)):
    return await chat_service.chat_history(db)
