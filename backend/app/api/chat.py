from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from typing import List
from sse_starlette.sse import EventSourceResponse

from app.core.database import get_db
from app.models.document import ChatMessage
from app.models.schemas import ChatRequest, ChatMessageResponse, ChatResponse
from app.services.rag import RAGService

router = APIRouter()

rag_service = RAGService()


@router.post("", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    db: AsyncSession = Depends(get_db),
):
    """Non-streaming RAG-based chat endpoint."""
    result = await rag_service.answer(
        question=request.message,
        document_ids=request.document_ids,
        top_k=5,
    )

    # Save to chat history
    chat_message = ChatMessage(
        role="user",
        content=request.message,
        document_ids=",".join(str(d) for d in request.document_ids) if request.document_ids else None,
    )
    db.add(chat_message)

    assistant_message = ChatMessage(
        role="assistant",
        content=result["answer"],
        document_ids=",".join(result["sources"]) if result["sources"] else None,
    )
    db.add(assistant_message)
    await db.commit()

    return ChatResponse(
        message=result["answer"],
        sources=result["sources"],
    )


@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    db: AsyncSession = Depends(get_db),
):
    async def event_generator():
        response_text = f"Echo: {request.message}"
        for chunk in response_text.split():
            yield {"event": "message", "data": chunk + " "}
        yield {"event": "done", "data": ""}

    return EventSourceResponse(event_generator())


@router.get("/history", response_model=List[ChatMessageResponse])
async def get_chat_history(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ChatMessage).order_by(ChatMessage.created_at.asc()))
    messages = result.scalars().all()
    return messages


@router.delete("/history")
async def clear_chat_history(db: AsyncSession = Depends(get_db)):
    await db.execute(delete(ChatMessage))
    await db.commit()
    return {"message": "Chat history cleared"}
