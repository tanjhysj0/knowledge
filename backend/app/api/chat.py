"""聊天路由层：仅做 HTTP/SSE 适配、依赖注入和服务调用。"""
from typing import List

from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.schemas import ChatMessageResponse, ChatRequest, ChatResponse
from app.services import chat as chat_service

router = APIRouter()


@router.post("", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    db: AsyncSession = Depends(get_db),
):
    """Non-streaming RAG-based chat endpoint."""
    result = await chat_service.ask(
        question=request.message,
        document_ids=request.document_ids,
        db=db,
    )
    return ChatResponse(message=result["answer"], sources=result["sources"])


@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    db: AsyncSession = Depends(get_db),
):
    """Streaming RAG-based chat with multi-turn context."""
    return EventSourceResponse(
        chat_service.stream_answer(
            question=request.message,
            document_ids=request.document_ids,
            db=db,
        )
    )


@router.get("/history", response_model=List[ChatMessageResponse])
async def get_chat_history(db: AsyncSession = Depends(get_db)):
    return await chat_service.chat_history(db)


@router.delete("/history")
async def delete_chat_history(db: AsyncSession = Depends(get_db)):
    await chat_service.clear_chat_history(db)
    return {"message": "Chat history cleared"}
