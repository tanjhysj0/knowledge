"""聊天应用服务：单轮问询、流式回答。

#36 起仅按 ``conversation_id`` 取历史上下文；写完消息后调
:func:`app.services.conversations.touch_conversation` 同步
``message_count`` / ``updated_at``。
"""
import json
import logging
import time
from typing import AsyncIterator, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.core.perf import elapsed_ms
from app.models.document import ChatMessage, Document
from app.services import conversations as conversation_service
from app.services.conversations import ConversationNotFoundError, touch_conversation
from app.services.rag import RAGService
from app.services.think_splitter import ThinkSplitter

logger = logging.getLogger(__name__)


class ChatServiceError(Exception):
    """聊天应用服务异常基类。"""


def _join_doc_ids(document_ids: List[int]) -> str | None:
    """把 ``document_ids`` 序列化为逗号分隔字符串，空列表返回 ``None``。"""
    if not document_ids:
        return None
    return ",".join(str(d) for d in document_ids)


async def _filter_ready_document_ids(
    db: AsyncSession, document_ids: List[int]
) -> List[int]:
    """#63：未 ``ready`` 的小说不参与 RAG 检索——检索前只保留 ready 状态的 id。

    后台索引写入向量前，小说处于 pending/processing/failed，不应被检索命中
    （包括写入中途残留的半成品向量）。空列表原样返回。
    """
    if not document_ids:
        return []
    result = await db.execute(
        select(Document.id).where(
            Document.id.in_(document_ids),
            Document.status == "ready",
        )
    )
    return list(result.scalars().all())


async def _ensure_conversation(db: AsyncSession, conversation_id: int) -> None:
    """验证会话存在，不存在抛 :class:`ConversationNotFoundError`。

    Pydantic schema 已强制 ``conversation_id`` 必填；这里只补一道业务校验，
    防止 race / 前端 stale id 写入孤儿消息。
    """
    await conversation_service.get_conversation(db, conversation_id)


def _is_evidence_pack(value) -> bool:
    """#66：value 是否为真实 EvidencePack（mock 场景下 MagicMock 应被排除）。"""
    from app.services.retrieval.evidence import EvidencePack

    return isinstance(value, EvidencePack)


def _evidence_detail(evidence_pack) -> List[Dict]:
    """#66：done 事件的结构化证据（doc_id/chapter/score/strategy）。"""
    if not _is_evidence_pack(evidence_pack):
        return []
    return [
        {
            "document_id": hit.document_id,
            "chunk_index": hit.chunk_index,
            "score": hit.score,
            "strategy": hit.strategy,
            "chapter": hit.chapter,
        }
        for hit in evidence_pack.hits
    ]


async def ask(
    question: str,
    document_ids: List[int],
    conversation_id: int,
    db: AsyncSession,
    request: Optional[Request] = None,
    strategies: Optional[List[str]] = None,
) -> Dict[str, object]:
    """调用 RAG 给出单轮答案，并落库 user + assistant 两条 ``ChatMessage``。

    返回 ``{"answer": str, "sources": list[str]}``。

    #75：``strategies`` 为检索策略白名单，透传给 :meth:`RAGService.aretrieve`
    （``None`` 不限定，行为与现状一致）。
    """
    # #36：会话必须存在；不存在则 Pydantic 业务层拑 404
    await _ensure_conversation(db, conversation_id)

    # #66：取当前问题之前的对话历史，透传给 Query Planner 做多轮指代消解。
    history_result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at.asc())
    )
    history = [
        {"role": msg.role, "content": msg.content}
        for msg in history_result.scalars().all()
    ]

    rag_service = RAGService(request=request)
    # 先检索：拿 sources 与 prompt 构造依据（#32 + #33）；
    # #63：未 ready 的小说不参与检索。
    retrieve_start = time.perf_counter()
    search_results = await rag_service.aretrieve(
        question=question,
        document_ids=await _filter_ready_document_ids(db, document_ids),
        history=history,
        strategies=strategies,
    )
    logger.info(
        "[perf] chat.retrieve hits=%d ms=%.1f",
        len(search_results),
        elapsed_ms(retrieve_start),
    )
    sources = rag_service._dedupe_sources(search_results)
    used_external = not search_results

    answer_start = time.perf_counter()
    if used_external:
        answer_text = await rag_service._llm().chat(
            messages=[{"role": "user", "content": rag_service._build_external_prompt(question)}]
        )
    else:
        answer_text = await rag_service._llm().chat(
            messages=[{"role": "user", "content": rag_service._build_rag_prompt(question, search_results)}]
        )
    logger.info(
        "[perf] chat.llm_answer external=%s ms=%.1f",
        used_external,
        elapsed_ms(answer_start),
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
    strategies: Optional[List[str]] = None,
) -> AsyncIterator[Dict[str, str]]:
    """流式产出 RAG 答案的 SSE 事件，并在流结束后落库 user + assistant 两条 ``ChatMessage``。

    事件顺序：``thinking`` → ``message`` → ``done``（带 sources）；
    LLM 调用或持久化过程中抛异常时，``db.rollback()`` 后只产出 ``error`` 事件。
    每条 ``data`` 字段是 ``json.dumps`` 序列化后的字符串。

    #36：多轮上下文仅按当前 ``conversation_id`` 取历史，并验证会话存在。
    #75：``strategies`` 为检索策略白名单，透传给 :meth:`RAGService.aretrieve`
    （``None`` 不限定，行为与现状一致）。
    """
    sources: List[str] = []
    full_answer = ""
    splitter = ThinkSplitter()
    total_start = time.perf_counter()

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
        # 先检索拿 sources + prompt 构造依据（#32 + #33）；
        # #63：未 ready 的小说不参与检索。
        # #66：aretrieve 内部跑混合检索管线（planner → 五路 → fusion →
        # rerank → 证据循环），契约不变。
        retrieve_start = time.perf_counter()
        search_results = await rag_service.aretrieve(
            question=question,
            document_ids=await _filter_ready_document_ids(db, document_ids),
            # #66：当前问题之前的历史（context_messages 末尾刚 append 了
            # 当前问题，切掉后传给 planner 做多轮指代消解）。
            history=context_messages[:-1],
            strategies=strategies,
        )
        logger.info(
            "[perf] chat.retrieve hits=%d ms=%.1f",
            len(search_results),
            elapsed_ms(retrieve_start),
        )
        sources = rag_service._dedupe_sources(search_results)
        used_external = not search_results

        if used_external:
            prompt = rag_service._build_external_prompt(question)
        else:
            prompt = rag_service._build_rag_prompt(question, search_results)
        messages = context_messages + [{"role": "user", "content": prompt}]

        # #66：可选 evidence 事件——证据包摘要（前端可后续消费，忽略不影响）。
        evidence_pack = rag_service.last_evidence_pack()
        if _is_evidence_pack(evidence_pack):
            yield {
                "event": "evidence",
                "data": json.dumps(evidence_pack.to_dict(), ensure_ascii=False),
            }

        llm_start = time.perf_counter()
        first_token_logged = False
        async for chunk_data in rag_service._llm().stream_chat(messages=messages):
            for kind, segment in splitter.feed(chunk_data):
                if not segment:
                    continue
                if not first_token_logged:
                    first_token_logged = True
                    logger.info(
                        "[perf] chat.llm_first_token ms=%.1f",
                        elapsed_ms(llm_start),
                    )
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

        logger.info(
            "[perf] chat.llm_stream_total external=%s ms=%.1f",
            used_external,
            elapsed_ms(llm_start),
        )

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

        logger.info("[perf] chat.total ms=%.1f", elapsed_ms(total_start))

        yield {
            "event": "done",
            "data": json.dumps(
                {
                    "sources": sources,
                    # #66：done 事件扩展结构化证据（doc_id/chapter/score/strategy），
                    # 向下兼容：sources 仍是字符串数组。
                    "evidence": _evidence_detail(evidence_pack),
                }
            ),
        }
    except Exception as exc:  # noqa: BLE001 — translate any failure to a single SSE error event
        await db.rollback()
        # #45 保持与 preflight 拒绝的 error 事件形状一致（``reason`` + ``error``），
        # 前端 LLMStatus banner 统一用 ``reason`` 作为展示文案。
        yield {
            "event": "error",
            "data": json.dumps({"reason": str(exc), "error": str(exc)}, ensure_ascii=False),
        }
