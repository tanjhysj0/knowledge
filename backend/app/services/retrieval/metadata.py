"""#66：Chapter / Entity / Event 三路元数据检索器。

三路检索都从 PostgreSQL 辅助索引表读线索，再由 ``bm25_chunks`` 表取原文
chunk 内容（不依赖向量存储，索引缺失时自动降级为空）：

- ChapterRetriever：query 中的"第X章/节/回"编号或标题关键词 → 命中章节
  覆盖的 chunk 区间。
- EntityRetriever：QueryPlan 实体线索或 query 中出现的实体名 → 命中实体
  锚点。
- EventRetriever：QueryPlan 事件线索或 query 与事件名重叠 → 命中事件锚点。
"""
import asyncio
import re
from typing import List, Optional

from sqlalchemy import select

from app.core.database import get_session_maker
from app.models.retrieval_index import Bm25Chunk, ChapterAnchor, EntityAnchor, EventAnchor
from app.services.retrieval import RetrievalHit
from app.services.retrieval.bm25 import tokenize
from app.services.retrieval.planner import QueryPlan

# 中文/英文章节标题模式：第X章 / 第X节 / 第X回 / Chapter X（X 支持中英数字）。
_CHAPTER_NO_RE = re.compile(r"第\s*([0-9零一二三四五六七八九十百千]+)\s*[章节回卷部]")
_CHAPTER_NO_EN_RE = re.compile(r"chapter\s+(\d+)", re.IGNORECASE)


def extract_chapter_no(text: str) -> Optional[int]:
    """从查询文本提取章节号（找不到返回 ``None``）。"""
    for pattern in (_CHAPTER_NO_RE, _CHAPTER_NO_EN_RE):
        match = pattern.search(text)
        if match:
            return _parse_chapter_number(match.group(1))
    return None


_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "百": 100, "千": 1000}


def _parse_chapter_number(raw: str) -> int:
    """中文数字/阿拉伯数字 → int（如 ``三`` → 3、``十二`` → 12）。"""
    if raw.isdigit():
        return int(raw)
    total, section, current = 0, 0, 0
    for char in raw:
        digit = _CN_DIGITS.get(char)
        if digit is None:
            continue
        if digit >= 10:
            section = (current or 1) * digit
            total += section
            current = 0
        else:
            current = digit
    return total + current


class _AnchorSessionMixin:
    """三路元数据检索器共用的 PG 会话访问（session 工厂可注入）。"""

    def __init__(self, session_factory=None):
        self._session_factory = session_factory or get_session_maker()


class ChapterRetriever(_AnchorSessionMixin):
    """章节检索器（策略名 ``chapter``）。"""

    strategy = "chapter"

    def decorate_query(self, query: str, plan: QueryPlan) -> str:
        """#74：把 QueryPlan 章节线索拼进检索词（管线不再感知策略名）。"""
        if plan.chapter_hints:
            return f"{query} {' '.join(plan.chapter_hints)}"
        return query

    async def retrieve(
        self,
        query: str,
        document_ids: Optional[List[int]] = None,
        top_k: int = 5,
    ) -> List[RetrievalHit]:
        if not query or not query.strip():
            return []
        chapter_no = extract_chapter_no(query)
        if chapter_no is None:
            return []
        try:
            hits = await self._load(chapter_no, document_ids, top_k)
        except Exception:  # noqa: BLE001 — 索引不可用时降级为空
            return []
        return hits

    async def _load(
        self,
        chapter_no: int,
        document_ids: Optional[List[int]],
        top_k: int,
    ) -> List[RetrievalHit]:
        async with self._session_factory() as db:
            anchor_stmt = select(ChapterAnchor).where(
                ChapterAnchor.chapter_no == chapter_no
            )
            if document_ids:
                anchor_stmt = anchor_stmt.where(
                    ChapterAnchor.document_id.in_(document_ids)
                )
            result = await db.execute(anchor_stmt)
            anchors = result.scalars().all()
            if not anchors:
                return []

            hits: List[RetrievalHit] = []
            for anchor in anchors:
                chunk_stmt = select(Bm25Chunk).where(
                    Bm25Chunk.document_id == anchor.document_id,
                    Bm25Chunk.chunk_index >= anchor.chunk_start,
                    Bm25Chunk.chunk_index < anchor.chunk_end,
                ).order_by(Bm25Chunk.chunk_index)
                chunks = (await db.execute(chunk_stmt)).scalars().all()
                hits.extend(
                    RetrievalHit(
                        document_id=chunk.document_id,
                        chunk_index=chunk.chunk_index,
                        content=chunk.content,
                        score=1.0,
                        strategy=self.strategy,
                        chapter=anchor.title,
                    )
                    for chunk in chunks[:top_k]
                )
            return hits[:top_k]


class EntityRetriever(_AnchorSessionMixin):
    """实体检索器（策略名 ``entity``）——由 QueryPlan 实体线索或 query 词驱动。"""

    strategy = "entity"

    def decorate_query(self, query: str, plan: QueryPlan) -> str:
        """#74：把 QueryPlan 实体线索拼进检索词（管线不再感知策略名）。"""
        if plan.entities:
            return f"{query} {' '.join(plan.entities)}"
        return query

    async def retrieve(
        self,
        query: str,
        document_ids: Optional[List[int]] = None,
        top_k: int = 5,
    ) -> List[RetrievalHit]:
        if not query or not query.strip():
            return []
        loop = asyncio.get_running_loop()
        tokens = set(await loop.run_in_executor(None, tokenize, query))
        try:
            return await self._load(tokens, document_ids, top_k)
        except Exception:  # noqa: BLE001 — 索引不可用时降级为空
            return []

    async def _load(
        self,
        tokens: set,
        document_ids: Optional[List[int]],
        top_k: int,
    ) -> List[RetrievalHit]:
        async with self._session_factory() as db:
            stmt = select(EntityAnchor).where(EntityAnchor.name.in_(tokens))
            if document_ids:
                stmt = stmt.where(EntityAnchor.document_id.in_(document_ids))
            result = await db.execute(stmt)
            anchors = result.scalars().all()
            return [
                RetrievalHit(
                    document_id=anchor.document_id,
                    chunk_index=anchor.chunk_index,
                    content=anchor.content,
                    score=1.0,
                    strategy=self.strategy,
                )
                for anchor in anchors[:top_k]
            ]


class EventRetriever(_AnchorSessionMixin):
    """事件检索器（策略名 ``event``）——query 词与事件名重叠即命中。"""

    strategy = "event"

    def decorate_query(self, query: str, plan: QueryPlan) -> str:
        """#74：把 QueryPlan 事件线索拼进检索词（管线不再感知策略名）。"""
        if plan.events:
            return f"{query} {' '.join(plan.events)}"
        return query

    async def retrieve(
        self,
        query: str,
        document_ids: Optional[List[int]] = None,
        top_k: int = 5,
    ) -> List[RetrievalHit]:
        if not query or not query.strip():
            return []
        loop = asyncio.get_running_loop()
        tokens = set(await loop.run_in_executor(None, tokenize, query))
        try:
            return await self._load(tokens, document_ids, top_k)
        except Exception:  # noqa: BLE001 — 索引不可用时降级为空
            return []

    async def _load(
        self,
        tokens: set,
        document_ids: Optional[List[int]],
        top_k: int,
    ) -> List[RetrievalHit]:
        async with self._session_factory() as db:
            result = await db.execute(select(EventAnchor))
            anchors = result.scalars().all()

        hits = []
        for anchor in anchors:
            if document_ids and anchor.document_id not in document_ids:
                continue
            name_tokens = set(self._tokenize_sync(anchor.name))
            if name_tokens & tokens:
                hits.append(
                    RetrievalHit(
                        document_id=anchor.document_id,
                        chunk_index=anchor.chunk_index,
                        content=anchor.content,
                        score=1.0,
                        strategy=self.strategy,
                    )
                )
        return hits[:top_k]

    @staticmethod
    def _tokenize_sync(text: str) -> List[str]:
        return tokenize(text)
