"""#81：GraphRetriever 单测——实体线索消费、一跳邻居检索、去重与降级。

覆盖验收：
- 装配后 v1 全量白名单包含 graph（在 test_chat_v1_whitelist 端到端验证）；
- 注入 fixture 图谱数据后，检索结果包含图回溯文本块（chunk 引用回填）；
- 多实体回溯到同一文本块时去重；图数据为空 / 无命中 / 会话异常静默空结果；
- graph 命中与其余路命中经 RRF 融合时按 chunk 去重（融合去重正确）。
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.models.graph import GraphEntity, GraphRelation
from app.services.retrieval.fusion import rrf_fusion
from app.services.retrieval.graph import GraphRetriever
from app.services.retrieval.planner import QueryPlan
from app.services.retrieval import RetrievalHit


def _entity(name, doc=1, chunk=0, content="c0"):
    return GraphEntity(
        document_id=doc, name=name, chunk_index=chunk, content=content,
    )


def _rel(subject, relation, obj, doc=1, chunk=0, content="c0"):
    return GraphRelation(
        document_id=doc, subject=subject, relation=relation, object=obj,
        chunk_index=chunk, content=content,
    )


class _FakeSession:
    """按语句目标表区分返回实体/关系行的 AsyncSession 替身（支持文档过滤）。"""

    def __init__(self, entities=None, relations=None, document_ids=None):
        self.entities = entities or []
        self.relations = relations or []
        self.document_ids = document_ids

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, statement):
        sql = str(statement).lower()
        rows = self.entities if "graph_entities" in sql else self.relations
        if self.document_ids is not None:
            rows = [r for r in rows if r.document_id in self.document_ids]
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: rows)
        )


class TestDecorateQuery:
    """#74：QueryPlan 实体线索经可选钩子拼进检索词。"""

    def test_entities_appended_to_query(self):
        retriever = GraphRetriever()
        query = retriever.decorate_query("原问题", QueryPlan(entities=["张三", "李四"]))
        assert query == "原问题 张三 李四"

    def test_no_entities_passthrough(self):
        retriever = GraphRetriever()
        assert retriever.decorate_query("原问题", QueryPlan(entities=[])) == "原问题"


class TestRetrieve:
    """fixture 图谱数据驱动的一跳邻居检索。"""

    @pytest.mark.asyncio
    async def test_hits_neighbors_with_source_chunk(self):
        """命中实体的出/入边关系都回溯到源文本块。"""
        retriever = GraphRetriever(
            session_factory=lambda: _FakeSession(
                entities=[_entity("张三"), _entity("李四"), _entity("主角")],
                relations=[
                    _rel("张三", "是", "主角", chunk=0, content="张三登场"),
                    _rel("李四", "击败", "张三", chunk=1, content="李四击败张三"),
                ],
            )
        )

        hits = await retriever.retrieve("张三", [1], top_k=5)

        assert [h.chunk_index for h in hits] == [0, 1]
        assert all(h.document_id == 1 for h in hits)
        assert all(h.strategy == "graph" for h in hits)
        assert all(h.score == 1.0 for h in hits)
        # 源文本块引用随命中返回（证据回溯）。
        assert hits[0].content == "张三登场"
        assert hits[1].content == "李四击败张三"

    @pytest.mark.asyncio
    async def test_multi_entity_same_chunk_deduped(self):
        """多实体回溯到同一文本块时去重（同一 chunk 只保留一条命中）。"""
        retriever = GraphRetriever(
            session_factory=lambda: _FakeSession(
                entities=[_entity("张三"), _entity("李四"), _entity("主角")],
                relations=[
                    _rel("张三", "是", "主角", chunk=0),
                    _rel("李四", "击败", "张三", chunk=0),
                ],
            )
        )

        # 张三与李四都命中：两条关系同属 chunk 0 → 去重为一条。
        hits = await retriever.retrieve("张三 李四", [1], top_k=5)

        assert len(hits) == 1
        assert hits[0].chunk_index == 0

    @pytest.mark.asyncio
    async def test_empty_graph_returns_empty(self):
        """图数据为空 → 该路静默空结果（不阻断整体问答）。"""
        retriever = GraphRetriever(session_factory=lambda: _FakeSession())
        assert await retriever.retrieve("张三", [1]) == []

    @pytest.mark.asyncio
    async def test_unknown_entity_returns_empty(self):
        """查询无命中（token 未匹配任何实体名）→ 空结果。"""
        retriever = GraphRetriever(
            session_factory=lambda: _FakeSession(entities=[_entity("主角")])
        )
        assert await retriever.retrieve("王五", [1]) == []

    @pytest.mark.asyncio
    async def test_document_ids_filter(self):
        """document_ids 过滤：只回溯指定文档的图数据。"""
        retriever = GraphRetriever(
            session_factory=lambda: _FakeSession(
                entities=[_entity("张三", doc=1), _entity("张三", doc=2)],
                relations=[
                    _rel("张三", "是", "主角", doc=1, chunk=0, content="d1"),
                    _rel("张三", "复仇", "主角", doc=2, chunk=0, content="d2"),
                ],
                document_ids=[1],
            )
        )

        hits = await retriever.retrieve("张三", [1], top_k=5)

        assert len(hits) == 1
        assert hits[0].document_id == 1
        assert hits[0].content == "d1"

    @pytest.mark.asyncio
    async def test_top_k_truncation(self):
        retriever = GraphRetriever(
            session_factory=lambda: _FakeSession(
                entities=[_entity("张三")],
                relations=[
                    _rel("张三", "r1", "a", chunk=0),
                    _rel("张三", "r2", "b", chunk=1),
                    _rel("张三", "r3", "c", chunk=2),
                ],
            )
        )

        hits = await retriever.retrieve("张三", [1], top_k=2)

        assert len(hits) == 2

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self):
        retriever = GraphRetriever(session_factory=lambda: _FakeSession())
        assert await retriever.retrieve("  ", [1]) == []

    @pytest.mark.asyncio
    async def test_session_failure_degrades_to_empty(self):
        """会话异常 → 静默空结果（图数据不可用不阻断整体问答）。"""

        def broken_factory():
            raise RuntimeError("pg down")

        retriever = GraphRetriever(session_factory=broken_factory)
        assert await retriever.retrieve("张三", [1]) == []


class TestFusionIntegration:
    """graph 命中进入 RRF 融合：与其余路命中同一 chunk 时按分去重。"""

    def test_graph_hit_deduped_with_dense_hit_same_chunk(self):
        graph_hit = RetrievalHit(
            document_id=1, chunk_index=0, content="c0", score=1.0,
            strategy="graph",
        )
        dense_hit = RetrievalHit(
            document_id=1, chunk_index=0, content="c0", score=0.9,
            strategy="dense",
        )

        fused = rrf_fusion(
            {"graph": [graph_hit], "dense": [dense_hit]}, top_n=5
        )

        # 同一 (document_id, chunk_index) 融合后只保留一条。
        assert len(fused) == 1

    def test_graph_hits_merge_with_other_routes(self):
        graph_hit = RetrievalHit(
            document_id=1, chunk_index=0, content="c0", score=1.0,
            strategy="graph",
        )
        bm25_hit = RetrievalHit(
            document_id=1, chunk_index=1, content="c1", score=0.8,
            strategy="bm25",
        )

        fused = rrf_fusion(
            {"graph": [graph_hit], "bm25": [bm25_hit]}, top_n=5
        )

        assert {h.chunk_index for h in fused} == {0, 1}


class TestAssemblyRegistration:
    """装配层登记一行：默认全开时 graph 路被组装。"""

    def test_build_retrievers_includes_graph(self):
        from app.services.retrieval.assembly import build_retrievers

        retriever = build_retrievers()["graph"]
        assert isinstance(retriever, GraphRetriever)
        assert retriever.strategy == "graph"
        assert retriever.switch == "retrieval_graph_enabled"

    def test_switch_off_excludes_graph(self):
        from app.services.retrieval import assembly

        with patch.object(assembly.settings, "retrieval_graph_enabled", False):
            assert "graph" not in assembly.build_retrievers()
