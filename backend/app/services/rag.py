import asyncio
from typing import List, Dict, Any, AsyncGenerator, Optional

from starlette.requests import Request

from app.services.embedding import get_embedding_provider
from app.services.vector_store import VectorStoreService
from app.services.llm import get_llm_provider
from app.services.retrieval import Retriever
from app.services.retrieval.assembly import build_retrievers
from app.core.config import get_settings

settings = get_settings()

# 向量存储对 COSINE 检索返回的 ``distance`` 字段实为余弦相似度
# （越大越相关，值域 [-1, 1]），与 L2 距离语义相反。
# 相似度低于该阈值的命中视为不相关、过滤掉（避免把无关文档塞进 prompt）。
RETRIEVAL_SCORE_THRESHOLD = 0.5


class RAGService:
    """Service for RAG-based question answering.

    #66：检索链路由单路 dense 升级为混合检索管线
    （Query Planner → Dense/BM25/Entity/Event/Chapter → RRF Fusion →
    Reranker → Evidence Agent 证据循环），见
    :class:`app.services.retrieval.pipeline.HybridRetrievalPipeline`。

    公开契约保持兼容：``aretrieve`` / ``answer`` / ``answer_stream`` 签名
    与返回结构不变；``sources`` 仍为 ``["doc_<id>", ...]``。
    ``used_external`` 语义改为"证据包完全为空"。

    ``_search_chunks`` / ``_asearch_chunks`` 保留（dense 单路检索，
    :class:`app.services.retrieval.dense.DenseRetriever` 为其迁移副本），
    供存量调用与单测使用。

    #74：检索器集合经构造注入（不实例化具体检索器类）；未注入时在装配层
    按 settings 开关组装（见 :mod:`app.services.retrieval.assembly`）。

    #75：可选 ``strategies`` 检索策略白名单逐层透传给混合检索管线（
    ``None`` 不限定，行为与 #74 一致）。
    """

    def __init__(
        self,
        request: Optional[Request] = None,
        retrievers: Optional[Dict[str, Retriever]] = None,
    ):
        self._vector_store = VectorStoreService()
        self._request = request
        # #74：检索器集合（key 为各检索器自描述的 strategy 名）构造注入；
        # settings 开关仅装配层感知，公用模块不感知。
        self._retrievers = (
            retrievers if retrievers is not None else build_retrievers()
        )
        # #66：最近一次 :meth:`retrieve_evidence` 的证据包（chat service 发
        # SSE ``evidence`` 事件用；RAGService 每次请求新建，无跨请求污染）。
        self._last_evidence_pack = None

    def _llm(self):
        """Resolve the current LLM provider on each call so settings changes take effect.

        The ``request`` propagated from the API route is forwarded to
        :func:`get_llm_provider` so the E2E ``X-E2E-Test`` header can swap in
        :class:`app.services.mock_llm.MockLLMProvider` without leaking the
        FastAPI ``Request`` into the rest of the business logic.
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

        命中会按 :data:`RETRIEVAL_SCORE_THRESHOLD`（COSINE 相似度）过滤：
        相似度低于阈值的命中视为"假相关"丢弃。

        mock embedding provider（见 :class:`app.services.embedding.mock`）返回全
        零向量，在 COSINE 检索下会触发相似度 1.0 的误命中。该场景
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
            # mock provider 短路：避免零向量在 COSINE 检索下触发误命中。
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
        except Exception:  # noqa: BLE001 — 向量存储不可用时回退 external
            return []

        filtered: List[Dict[str, Any]] = []
        for hit in raw_hits or []:
            similarity = hit.get("distance")
            if similarity is not None and float(similarity) < RETRIEVAL_SCORE_THRESHOLD:
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

    async def retrieve_evidence(
        self,
        question: str,
        document_ids: List[int],
        top_k: int = 5,
        history: Optional[List[Dict[str, str]]] = None,
        strategies: Optional[List[str]] = None,
    ):
        """#66：完整证据管线入口（规划 → 混合检索 → 融合 → 重排 → 证据循环）。

        返回 :class:`~app.services.retrieval.evidence.EvidencePack`；同时
        缓存在 ``self`` 上供 :meth:`last_evidence_pack` 读取（SSE evidence
        事件）。

        #75：``strategies`` 为调用方检索策略白名单，透传给管线（``None``
        不限定）。
        """
        from app.services.retrieval.evidence import EvidencePack
        from app.services.retrieval.pipeline import HybridRetrievalPipeline

        pipeline = HybridRetrievalPipeline(
            retrievers=self._retrievers,
            request=self._request,
            top_k=top_k,
            strategies=strategies,
        )
        pack: EvidencePack = await pipeline.retrieve(
            question, document_ids or [], history
        )
        self._last_evidence_pack = pack
        return pack

    def last_evidence_pack(self):
        """最近一次 :meth:`retrieve_evidence` 的证据包（可能为 ``None``）。"""
        return self._last_evidence_pack

    @staticmethod
    def _hit_to_legacy_dict(hit) -> Dict[str, Any]:
        """EvidencePack 命中 → 旧检索结果 dict（``distance`` 沿用相似度语义）。"""
        return {
            "document_id": hit.document_id,
            "chunk_index": hit.chunk_index,
            "content": hit.content,
            "distance": hit.score,
        }

    async def aretrieve(
        self,
        question: str,
        document_ids: List[int],
        top_k: Optional[int] = None,
        history: Optional[List[Dict[str, str]]] = None,
        strategies: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """公开的检索入口：跑混合检索管线，返回旧格式命中 dict 列表。

        供 :mod:`app.services.chat` 在拼装 prompt 前调用，以便 SSE ``done``
        事件能携带 sources；不在本方法内做 prompt 构造 / LLM 调用。

        ``history`` 透传给 Query Planner 做多轮指代消解（当前问题之前
        的对话历史）；``strategies``（#75）为检索策略白名单，透传给
        ``retrieve_evidence``（``None`` 不限定）。
        """
        pack = await self.retrieve_evidence(
            question, document_ids, top_k, history, strategies
        )
        return [self._hit_to_legacy_dict(hit) for hit in pack.hits]

    async def answer(
        self,
        question: str,
        document_ids: List[int] = None,
        top_k: int = 5,
    ) -> Dict[str, Any]:
        """Answer a question using RAG. Returns dict with 'answer', 'sources', 'used_external'."""
        search_results = await self.aretrieve(question, document_ids or [], top_k)
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
        search_results = await self.aretrieve(question, document_ids or [], top_k)
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
