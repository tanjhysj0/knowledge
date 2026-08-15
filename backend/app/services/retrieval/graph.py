"""#81：Graph 检索器——QueryPlan 实体线索驱动的图一跳邻居检索。

策略名 ``graph``：``decorate_query`` 把 QueryPlan 实体线索拼进检索词
（与 entity 检索器一致）；``retrieve`` 先按分词后的 token 集合匹配
``graph_entities`` 表得到精确实体名，再查这些实体在图中的一跳邻居关系，
回溯源文本块（``GraphRelation`` 自带 chunk 引用）返回检索命中。

多实体回溯到同一文本块时去重（同一 chunk 只保留一条命中）；图数据为空 /
查询无命中 / 会话异常均静默返回空列表（与其余各路降级契约一致，不阻断
整体问答）。
"""
import asyncio
from typing import List, Optional, Set

from sqlalchemy import or_, select

from app.core.database import get_session_maker
from app.models.graph import GraphEntity, GraphRelation
from app.services.retrieval import RetrievalHit
from app.services.retrieval.bm25 import tokenize
from app.services.retrieval.planner import QueryPlan


class GraphRetriever:
    """图谱一跳邻居检索器（策略名 ``graph``）。

    session 工厂可注入（单测用 fake session）；默认走应用全局工厂。
    检索异常由内部吞掉并返回空列表——图数据缺失不影响整体问答。
    """

    strategy = "graph"
    # #79：settings 开关字段名自描述（装配层按类属性过滤，无独立映射表）。
    switch = "retrieval_graph_enabled"

    def __init__(self, session_factory=None):
        self._session_factory = session_factory or get_session_maker()

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
        except Exception:  # noqa: BLE001 — 图数据不可用时降级为空
            return []

    async def _load(
        self,
        tokens: Set[str],
        document_ids: Optional[List[int]],
        top_k: int,
    ) -> List[RetrievalHit]:
        async with self._session_factory() as db:
            entity_stmt = select(GraphEntity).where(
                GraphEntity.name.in_(tokens)
            )
            if document_ids:
                entity_stmt = entity_stmt.where(
                    GraphEntity.document_id.in_(document_ids)
                )
            entities = (await db.execute(entity_stmt)).scalars().all()
            if not entities:
                return []
            names = {e.name for e in entities}
            rel_stmt = select(GraphRelation).where(
                or_(
                    GraphRelation.subject.in_(names),
                    GraphRelation.object.in_(names),
                )
            )
            if document_ids:
                rel_stmt = rel_stmt.where(
                    GraphRelation.document_id.in_(document_ids)
                )
            relations = (await db.execute(rel_stmt)).scalars().all()
        return _relations_to_hits(relations, top_k)


def _relations_to_hits(relations, top_k: int) -> List[RetrievalHit]:
    """关系行 → 检索命中（多实体回溯同一文本块只保留一条）。

    subject/object 任一命中实体即代表该 chunk 与图谱相关；同一
    (document_id, chunk_index) 只保留一条命中，score 沿用元数据路的
    固定 1.0（RRF 融合阶段按 rank 重算）。
    """
    hits: List[RetrievalHit] = []
    seen: Set[tuple] = set()
    for rel in relations:
        key = (rel.document_id, rel.chunk_index)
        if key in seen:
            continue
        seen.add(key)
        hits.append(
            RetrievalHit(
                document_id=rel.document_id,
                chunk_index=rel.chunk_index,
                content=rel.content,
                score=1.0,
                strategy="graph",
            )
        )
    return hits[:top_k]
