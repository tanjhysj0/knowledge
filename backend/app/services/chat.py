"""聊天应用服务：单轮问询、流式回答。

#36 起仅按 ``conversation_id`` 取历史上下文；写完消息后调
:func:`app.services.conversations.touch_conversation` 同步
``message_count`` / ``updated_at``。
"""
import json
from typing import AsyncIterator, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.models.document import ChatMessage
from app.services import conversations as conversation_service
from app.services.conversations import ConversationNotFoundError, touch_conversation
from app.services.rag import RAGService
from app.services.think_splitter import ThinkSplitter


class ChatServiceError(Exception):
    """聊天应用服务异常基类。"""


def _join_doc_ids(document_ids: List[int]) -> str | None:
    """把 ``document_ids`` 序列化为逗号分隔字符串，空列表返回 ``None``。"""
    if not document_ids:
        return None
    return ",".join(str(d) for d in document_ids)


async def _ensure_conversation(db: AsyncSession, conversation_id: int) -> None:
    """验证会话存在，不存在抛 :class:`ConversationNotFoundError`。

    Pydantic schema 已强制 ``conversation_id`` 必填；这里只补一道业务校验，
    防止 race / 前端 stale id 写入孤儿消息。
    """
    await conversation_service.get_conversation(db, conversation_id)


async def ask(
    question: str,
    document_ids: List[int],
    conversation_id: int,
    db: AsyncSession,
    request: Optional[Request] = None,
) -> Dict[str, object]:
    """调用 RAG 给出单轮答案，并落库 user + assistant 两条 ``ChatMessage``。

    返回 ``{"answer": str, "sources": list[str]}``。
    """
    # #36：会话必须存在；不存在则 Pydantic 业务层拑 404
    await _ensure_conversation(db, conversation_id)

    rag_service = RAGService(request=request)
    # 先检索：拿 sources 与 prompt 构造依据（#32 + #33）
    search_results = await rag_service.aretrieve(
        question=question,
        document_ids=document_ids,
        top_k=5,
    )
    sources = rag_service._dedupe_sources(search_results)
    used_external = not search_results

    if used_external:
        answer_text = await rag_service._llm().chat(
            messages=[{"role": "user", "content": rag_service._build_external_prompt(question)}]
        )
    else:
        answer_text = await rag_service._llm().chat(
            messages=[{"role": "user", "content": rag_service._build_rag_prompt(question, search_results)}]
        )

    db.add(
        ChatMessage(
            role="user",
            content=question,
            document_ids=_join_doc_ids(document_ids),
            conversation_id=conversation_id,
        )
    )
    db.add(
        ChatMessage(
            role="assistant",
            content=answer_text,
            document_ids=_join_doc_ids(sources),
            conversation_id=conversation_id,
        )
    )
    await db.commit()
    # #36：同步会话 message_count / updated_at
    await touch_conversation(db, conversation_id, delta=2)

    return {"answer": answer_text, "sources": sources}


async def stream_answer(
    question: str,
    document_ids: List[int],
    conversation_id: int,
    db: AsyncSession,
    request: Optional[Request] = None,
) -> AsyncIterator[Dict[str, str]]:
    """流式产出 RAG 答案的 SSE 事件，并在流结束后落库 user + assistant 两条 ``ChatMessage``。

    事件顺序：``thinking`` → ``message`` → ``done``（带 sources）；
    LLM 调用或持久化过程中抛异常时，``db.rollback()`` 后只产出 ``error`` 事件。
    每条 ``data`` 字段是 ``json.dumps`` 序列化后的字符串。

    #36：多轮上下文仅按当前 ``conversation_id`` 取历史，并验证会话存在。
    """
    sources: List[str] = []
    full_answer = ""
    splitter = ThinkSplitter()

    try:
        # #36：会话必须存在 → 不存在直接报 404
        await _ensure_conversation(db, conversation_id)

        history_result = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.created_at.asc())
        )
        history_messages = history_result.scalars().all()
        context_messages: List[Dict[str, str]] = [
            {"role": msg.role, "content": msg.content} for msg in history_messages
        ]

        user_msg = ChatMessage(
            role="user",
            content=question,
            document_ids=_join_doc_ids(document_ids),
            conversation_id=conversation_id,
        )
        db.add(user_msg)
        await db.flush()

        context_messages.append({"role": "user", "content": question})

        rag_service = RAGService(request=request)
        # 先检索拿 sources + prompt 构造依据（#32 + #33）。
        search_results = await rag_service.aretrieve(
            question=question,
            document_ids=document_ids,
            top_k=5,
        )
        sources = rag_service._dedupe_sources(search_results)
        used_external = not search_results

        if used_external:
            prompt = rag_service._build_external_prompt(question)
        else:
            prompt = rag_service._build_rag_prompt(question, search_results)
        messages = context_messages + [{"role": "user", "content": prompt}]

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
            conversation_id=conversation_id,
        )
        db.add(assistant_msg)
        await db.flush()
        await db.commit()
        # #36：同步会话 message_count / updated_at
        await touch_conversation(db, conversation_id, delta=2)

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
