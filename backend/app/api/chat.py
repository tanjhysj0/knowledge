from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from typing import List
from sse_starlette.sse import EventSourceResponse

from app.core.database import get_db
from app.models.document import ChatMessage
from app.models.schemas import ChatRequest, ChatMessageResponse

router = APIRouter()


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
