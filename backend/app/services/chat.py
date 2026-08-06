"""聊天应用服务：单轮问询、流式回答、历史读写。"""
import json
from typing import AsyncIterator, Dict, List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.models.document import ChatMessage
from app.services.rag import RAGService
from app.services.think_splitter import ThinkSplitter


class ChatServiceError(Exception):
    """聊天应用服务异常基类。"""


def _join_doc_ids(document_ids: List[int]) -> str | None:
    """把 ``document_ids`` 序列化为逗号分隔字符串，空列表返回 ``None``。"""
    if not document_ids:
        return None
    return ",".join(str(d) for d in document_ids)


async def ask(
    question: str,
    document_ids: List[int],
    db: AsyncSession,
    request: Optional[Request] = None,
) -> Dict[str, object]:
    """调用 RAG 给出单轮答案，并落库 user + assistant 两条 ``ChatMessage``。

    返回 ``{"answer": str, "sources": list[str]}``。
    """
    rag_service = RAGService(request=request)
    result = await rag_service.answer(
        question=question,
        document_ids=document_ids,
        top_k=5,
    )

    db.add(
        ChatMessage(
            role="user",
            content=question,
            document_ids=_join_doc_ids(document_ids),
        )
    )
    db.add(
        ChatMessage(
            role="assistant",
            content=result["answer"],
            document_ids=_join_doc_ids(result["sources"]),
        )
    )
    await db.commit()

    return {"answer": result["answer"], "sources": result["sources"]}


async def stream_answer(
    question: str,
    document_ids: List[int],
    db: AsyncSession,
    request: Optional[Request] = None,
) -> AsyncIterator[Dict[str, str]]:
    """流式产出 RAG 答案的 SSE 事件，并在流结束后落库 user + assistant 两条 ``ChatMessage``。

    事件顺序：``thinking`` → ``message`` → ``done``（带 sources）；
    LLM 调用或持久化过程中抛异常时，``db.rollback()`` 后只产出 ``error`` 事件。
    每条 ``data`` 字段是 ``json.dumps`` 序列化后的字符串。
    """
    sources: List[str] = []
    full_answer = ""
    splitter = ThinkSplitter()

    try:
        history_result = await db.execute(
            select(ChatMessage).order_by(ChatMessage.created_at.asc())
        )
        history_messages = history_result.scalars().all()
        context_messages: List[Dict[str, str]] = [
            {"role": msg.role, "content": msg.content} for msg in history_messages
        ]

        user_msg = ChatMessage(
            role="user",
            content=question,
            document_ids=_join_doc_ids(document_ids),
        )
        db.add(user_msg)
        await db.flush()

        context_messages.append({"role": "user", "content": question})

        # Streaming answer; RAG retrieval is disabled (no embedding provider)
        prompt = (
            f"Question: {question}\n\n"
            "Please answer this question based on your general knowledge."
        )
        messages = context_messages + [{"role": "user", "content": prompt}]

        rag_service = RAGService(request=request)
        async for chunk_data in rag_service._llm().stream_chat(messages=messages):
            for kind, segment in splitter.feed(chunk_data):
                if not segment:
                    continue
                if kind == "thinking":
                    yield {
                        "event": "thinking",
                        "data": json.dumps({"content": segment}, ensure_ascii=False),
                    }
                else:
                    full_answer += segment
                    yield {
                        "event": "message",
                        "data": json.dumps({"content": segment}, ensure_ascii=False),
                    }

        for kind, segment in splitter.flush():
            if not segment:
                continue
            if kind == "thinking":
                yield {
                    "event": "thinking",
                    "data": json.dumps({"content": segment}, ensure_ascii=False),
                }
            else:
                full_answer += segment
                yield {
                    "event": "message",
                    "data": json.dumps({"content": segment}, ensure_ascii=False),
                }

        assistant_msg = ChatMessage(
            role="assistant",
            content=full_answer,
            document_ids=_join_doc_ids(sources),
        )
        db.add(assistant_msg)
        await db.flush()
        await db.commit()

        yield {
            "event": "done",
            "data": json.dumps({"sources": sources}),
        }
    except Exception as exc:  # noqa: BLE001 — translate any failure to a single SSE error event
        await db.rollback()
        yield {
            "event": "error",
            "data": json.dumps({"error": str(exc)}),
        }


async def chat_history(db: AsyncSession) -> List[ChatMessage]:
    """按 ``created_at`` 升序返回全部 ``ChatMessage``。"""
    result = await db.execute(
        select(ChatMessage).order_by(ChatMessage.created_at.asc())
    )
    return list(result.scalars().all())


async def clear_chat_history(db: AsyncSession) -> None:
    """删除全部 ``ChatMessage`` 行并提交。"""
    await db.execute(delete(ChatMessage))
    await db.commit()
