"""#66：Dense 检索器——复用现有 bge-m3 embedding + PG/pgvector COSINE 检索。

逻辑自 ``RAGService._search_chunks`` 迁移而来（阈值过滤保留），异常
一律吞掉返回空列表（单路降级不阻断整体问答）。
"""
import asyncio
from typing import List, Optional

from app.services.embedding import get_embedding_provider
from app.services.retrieval import RetrievalHit
from app.services.vector_store import VectorStoreService

# 迁移自 rag.RETRIEVAL_SCORE_THRESHOLD：向量存储对 COSINE 检索返回的
# ``distance`` 字段实为余弦相似度（越大越相关），低于该阈值视为假命中。
SCORE_THRESHOLD = 0.5


class DenseRetriever:
    """PG/pgvector dense 向量检索（策略名 ``dense``）。"""

    name = "dense"

    def __init__(self, vector_store: Optional[VectorStoreService] = None):
        # 底层存储可注入（单测 mock）；默认每次构造新实例与旧行为一致。
        self._vector_store = vector_store or VectorStoreService()

    def _search_sync(
        self,
        query: str,
        document_ids: Optional[List[int]],
        top_k: int,
    ) -> List[RetrievalHit]:
        if not query or not query.strip():
            return []

        try:
            provider = get_embedding_provider()
            # mock provider 短路：零向量在 COSINE 检索下会误命中。
            # 严格 ``is True`` 判断，避免 MagicMock 真值在单测里误短路。
            if getattr(provider, "is_mock", False) is True:
                return []
            query_vectors = provider.embed_texts([query])
        except Exception:  # noqa: BLE001 — embedding 不可用时降级为空
            return []

        if not query_vectors:
            return []

        try:
            raw_hits = self._vector_store.search(
                query_embedding=query_vectors[0],
                limit=top_k,
                document_ids=document_ids or None,
            )
        except Exception:  # noqa: BLE001 — 向量存储不可用时降级为空
            return []

        hits: List[RetrievalHit] = []
        for hit in raw_hits or []:
            similarity = hit.get("distance")
            if similarity is not None and float(similarity) < SCORE_THRESHOLD:
                continue
            hits.append(
                RetrievalHit(
                    document_id=hit.get("document_id"),
                    chunk_index=hit.get("chunk_index"),
                    content=hit.get("content") or "",
                    score=float(similarity) if similarity is not None else 0.0,
                    strategy=self.name,
                )
            )
        return hits

    async def retrieve(
        self,
        query: str,
        document_ids: Optional[List[int]] = None,
        top_k: int = 5,
    ) -> List[RetrievalHit]:
        """异步检索：同步阻塞的 embedding/向量调用丢到默认 executor。"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._search_sync, query, document_ids, top_k
        )
