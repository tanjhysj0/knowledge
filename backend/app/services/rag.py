import asyncio
from typing import List, Dict, Any, AsyncGenerator, Optional

from starlette.requests import Request

from app.services.embedding import get_embedding_provider
from app.services.vector_store import VectorStoreService
from app.services.llm import get_llm_provider
from app.core.config import get_settings

settings = get_settings()

# Milvus COSINE metric 下 ``distance = 1 - cosine_similarity``，越小越相似。
# 大于该阈值的命中视为不相关、过滤掉（避免把无关文档塞进 prompt）。
RETRIEVAL_SCORE_THRESHOLD = 0.5


class RAGService:
    """Service for RAG-based question answering.

    检索链路（#32 真打开）：

    1. :meth:`_search_chunks` 把 ``question`` 送 embedding provider 取 query 向量
    2. 调 :meth:`VectorStoreService.search` 在 Milvus 中按 cosine 距离召回 top-k
    3. 用 :data:`RETRIEVAL_SCORE_THRESHOLD` 过滤掉距离过大的"假命中"
    4. 命中非空时拼 :meth:`_build_rag_prompt`；未命中或检索异常时回退
       :meth:`_build_external_prompt`（与历史行为兼容）
    5. ``sources`` 字段按 ``document_id`` 去重，仅返回 ``["doc_<id>", ...]``
    """

    def __init__(self, request: Optional[Request] = None):
        self._vector_store = VectorStoreService()
        self._request = request

    def _llm(self):
        """Resolve the current LLM provider on each call so settings changes take effect.

        The ``request`` propagated from the API route is forwarded to
        :func:`get_llm_provider` so the E2E ``X-E2E-Test`` header can swap in
        :class:`MockLLMProvider` without leaking the FastAPI ``Request`` into
        the rest of the business logic.
        """
        return get_llm_provider(self._request)

    def _build_rag_prompt(self, question: str, context_chunks: List[Dict[str, Any]]) -> str:
        if not context_chunks:
            return f"Question: {question}\n\nPlease answer based on your general knowledge."

        context_text = "\n\n".join(
            f"[Document {i+1}]\n{chunk.get('content', '')}"
            for i, chunk in enumerate(context_chunks)
        )

        return f"""Based on the following context, answer the question. If the context doesn't contain relevant information, say so.

Context:
{context_text}

Question: {question}

Answer:"""

    def _build_external_prompt(self, question: str) -> str:
        return f"""Question: {question}

Please answer this question based on your general knowledge."""

    def _search_chunks(
        self,
        question: str,
        document_ids: List[int],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """同步检索相关 chunks；空问题 / embedding 失败 / 向量库异常都返回 ``[]``。

        命中会按 :data:`RETRIEVAL_SCORE_THRESHOLD`（COSINE distance）过滤：
        大于阈值的命中视为"假相关"丢弃。

        mock embedding provider（见 :class:`app.services.embedding.mock`）返回全
        零向量，在 Milvus COSINE metric 下会触发距离=0 的误命中。该场景
        （``provider.is_mock`` 为真）下直接短路为空，避免污染 RAG prompt 与
        ``sources``。
        """
        if not question or not question.strip():
            return []
        if not document_ids:
            # 没有指定文档时仍然检索（按 query 拉 top-k），由调用方决定是否使用
            pass

        try:
            provider = get_embedding_provider()
            # mock provider 短路：避免零向量在 Milvus 触发误命中。
            # 用严格 ``is True`` 判断，避免 ``MagicMock().is_mock`` 返回
            # 另一个 ``MagicMock``（真值）在单测里误命中短路。
            if getattr(provider, "is_mock", False) is True:
                return []
            query_vectors = provider.embed_texts([question])
        except Exception:  # noqa: BLE001 — embedding 不可用时静默回退 external
            return []

        if not query_vectors:
            return []

        try:
            raw_hits = self._vector_store.search(
                query_embedding=query_vectors[0],
                limit=top_k,
                document_ids=document_ids or None,
            )
        except Exception:  # noqa: BLE001 — 向量库不可用时回退 external
            return []

        filtered: List[Dict[str, Any]] = []
        for hit in raw_hits or []:
            distance = hit.get("distance")
            if distance is not None and float(distance) > RETRIEVAL_SCORE_THRESHOLD:
                continue
            filtered.append(hit)
        return filtered

    async def _asearch_chunks(
        self,
        question: str,
        document_ids: List[int],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """异步包装 ``_search_chunks``：把同步阻塞调用丢到默认 executor。

        bge-m3 encode 是 CPU 密集型；直接 await 会阻塞事件循环。后续若
        embedding provider 暴露 async API，可在此切换。
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._search_chunks, question, document_ids, top_k
        )

    @staticmethod
    def _dedupe_sources(search_results: List[Dict[str, Any]]) -> List[str]:
        """按 ``document_id`` 去重，输出 ``["doc_<id>", ...]``。"""
        sources: List[str] = []
        seen: set = set()
        for result in search_results or []:
            doc_id = result.get("document_id")
            if doc_id is None or doc_id in seen:
                continue
            seen.add(doc_id)
            sources.append(f"doc_{doc_id}")
        return sources

    async def aretrieve(
        self,
        question: str,
        document_ids: List[int],
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """公开的检索入口：返回原始命中 dict 列表（与 :meth:`_asearch_chunks` 等价）。

        供 :mod:`app.services.chat` 在拼装 prompt 前调用，以便 SSE ``done``
        事件能携带 sources；不在本方法内做 prompt 构造 / LLM 调用。
        """
        return await self._asearch_chunks(question, document_ids, top_k)

    async def answer(
        self,
        question: str,
        document_ids: List[int] = None,
        top_k: int = 5,
    ) -> Dict[str, Any]:
        """Answer a question using RAG. Returns dict with 'answer', 'sources', 'used_external'."""
        search_results = await self._asearch_chunks(question, document_ids or [], top_k)
        used_external = not search_results

        prompt = (
            self._build_external_prompt(question)
            if used_external
            else self._build_rag_prompt(question, search_results)
        )
        messages = [{"role": "user", "content": prompt}]
        answer_text = await self._llm().chat(messages)

        return {
            "answer": answer_text,
            "sources": self._dedupe_sources(search_results),
            "used_external": used_external,
        }

    async def answer_stream(
        self,
        question: str,
        document_ids: List[int] = None,
        top_k: int = 5,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Answer a question with streaming. Yields ``{chunk, done, sources, error}``."""
        search_results = await self._asearch_chunks(question, document_ids or [], top_k)
        used_external = not search_results

        prompt = (
            self._build_external_prompt(question)
            if used_external
            else self._build_rag_prompt(question, search_results)
        )
        messages = [{"role": "user", "content": prompt}]

        sources = self._dedupe_sources(search_results)

        try:
            full_answer = ""
            async for chunk in self._llm().stream_chat(messages=messages):
                full_answer += chunk
                yield {"chunk": chunk, "done": False, "sources": sources, "error": None}
            yield {"chunk": "", "done": True, "sources": sources, "error": None}
        except Exception as e:
            yield {"chunk": "", "done": True, "sources": sources, "error": str(e)}
