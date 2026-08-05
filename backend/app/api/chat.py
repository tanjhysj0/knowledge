from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from typing import List
from sse_starlette.sse import EventSourceResponse
import json

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
    """Streaming RAG-based chat with multi-turn context."""
    user_message_id = None
    assistant_message_id = None

    async def event_generator():
        nonlocal user_message_id, assistant_message_id
        sources = []
        full_answer = ""

        try:
            history_result = await db.execute(
                select(ChatMessage).order_by(ChatMessage.created_at.asc())
            )
            history_messages = history_result.scalars().all()

            context_messages = []
            for msg in history_messages:
                context_messages.append({"role": msg.role, "content": msg.content})

            user_msg = ChatMessage(
                role="user",
                content=request.message,
                document_ids=",".join(str(d) for d in request.document_ids) if request.document_ids else None,
            )
            db.add(user_msg)
            await db.flush()
            user_message_id = user_msg.id

            context_messages.append({"role": "user", "content": request.message})

            # Streaming answer; RAG retrieval is disabled (no embedding provider)
            prompt = f"Question: {request.message}\n\nPlease answer this question based on your general knowledge."
            messages = context_messages + [{"role": "user", "content": prompt}]

            async for chunk_data in rag_service._llm.stream_chat(messages=messages):
                full_answer += chunk_data
                yield {
                    "event": "message",
                    "data": json.dumps({"content": chunk_data}),
                }

            assistant_msg = ChatMessage(
                role="assistant",
                content=full_answer,
                document_ids=",".join(sources) if sources else None,
            )
            db.add(assistant_msg)
            await db.flush()
            assistant_message_id = assistant_msg.id

            await db.commit()

            yield {
                "event": "done",
                "data": json.dumps({"sources": sources}),
            }

        except Exception as e:
            await db.rollback()
            yield {
                "event": "error",
                "data": json.dumps({"error": str(e)}),
            }

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