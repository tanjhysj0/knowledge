"""#66：BM25 检索器——jieba 分词 + 应用层 BM25 评分。

方案选择（见 ADR-0005）：不依赖 Milvus sparse/BM25 function，也不依赖
PostgreSQL 中文全文检索扩展（zhparser 需额外安装）。索引构建阶段把每个
chunk 的 jieba tokens 存入 ``bm25_chunks`` 表，检索时按 ``document_ids``
加载候选 chunk 后在应用层算 BM25（小说级语料单次加载规模可控）。
"""
import asyncio
import math
from collections import Counter
from typing import Dict, List, Optional

import jieba
from sqlalchemy import select

from app.core.database import get_session_maker
from app.models.retrieval_index import Bm25Chunk
from app.services.retrieval import RetrievalHit

# BM25 标准参数。
K1 = 1.5
B = 0.75


def tokenize(text: str) -> List[str]:
    """jieba 精确模式分词；空文本返回空列表。"""
    return [t for t in jieba.lcut(text.strip()) if t.strip()]


def bm25_score(
    query_tokens: List[str],
    chunk_tokens: List[str],
    df: Dict[str, int],
    total_chunks: int,
    avg_chunk_len: float,
) -> float:
    """单 chunk 的 BM25 分数（纯函数，便于单测）。"""
    if not chunk_tokens:
        return 0.0
    counter = Counter(chunk_tokens)
    doc_len = len(chunk_tokens)
    score = 0.0
    for token in set(query_tokens):
        freq = counter.get(token, 0)
        if freq == 0:
            continue
        doc_freq = df.get(token, 0)
        idf = math.log(1 + (total_chunks - doc_freq + 0.5) / (doc_freq + 0.5))
        norm = freq * (K1 + 1) / (freq + K1 * (1 - B + B * doc_len / max(avg_chunk_len, 1.0)))
        score += idf * norm
    return score


class BM25Retriever:
    """基于 ``bm25_chunks`` 表的 BM25 检索（策略名 ``bm25``）。"""

    name = "bm25"

    def __init__(self, session_factory=None):
        # session 工厂可注入（单测用 fake session）；默认走应用全局工厂。
        self._session_factory = session_factory or get_session_maker()

    async def _load_chunks(
        self, document_ids: Optional[List[int]]
    ) -> List[Bm25Chunk]:
        async with self._session_factory() as db:
            stmt = select(Bm25Chunk)
            if document_ids:
                stmt = stmt.where(Bm25Chunk.document_id.in_(document_ids))
            result = await db.execute(stmt)
            return list(result.scalars().all())

    async def retrieve(
        self,
        query: str,
        document_ids: Optional[List[int]] = None,
        top_k: int = 5,
    ) -> List[RetrievalHit]:
        """按查询词对候选 chunk 做 BM25 评分，返回 top-k。"""
        if not query or not query.strip():
            return []
        try:
            chunks = await self._load_chunks(document_ids)
        except Exception:  # noqa: BLE001 — 索引表不可用时降级为空
            return []
        if not chunks:
            return []

        loop = asyncio.get_running_loop()
        query_tokens = await loop.run_in_executor(None, tokenize, query)
        if not query_tokens:
            return []

        # df：每个查询词命中的 chunk 数。
        df: Dict[str, int] = {t: 0 for t in set(query_tokens)}
        for chunk in chunks:
            for token in set(chunk.tokens or []):
                if token in df:
                    df[token] += 1

        total = len(chunks)
        avg_len = (
            sum(len(c.tokens or []) for c in chunks) / total if total else 0.0
        )

        scored = []
        for chunk in chunks:
            score = bm25_score(query_tokens, chunk.tokens or [], df, total, avg_len)
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda pair: pair[0], reverse=True)

        return [
            RetrievalHit(
                document_id=chunk.document_id,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                score=score,
                strategy=self.name,
            )
            for score, chunk in scored[:top_k]
        ]
