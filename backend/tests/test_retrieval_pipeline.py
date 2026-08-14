"""#66：HybridRetrievalPipeline 编排单测（全部组件 mock）。

验证数据流：planner → 五路检索 → RRF 融合 → reranker → agent，以及
各类降级路径（首个子查询命中即停、单路失败、未知策略跳过）。
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.retrieval import RetrievalHit
from app.services.retrieval.evidence import EvidencePack
from app.services.retrieval.pipeline import HybridRetrievalPipeline
from app.services.retrieval.planner import QueryPlan


def _hit(doc_id: int, chunk: int, score: float = 0.8) -> RetrievalHit:
    return RetrievalHit(
        document_id=doc_id, chunk_index=chunk, content=f"c{chunk}",
        score=score, strategy="dense",
    )


def _retriever(name: str, hits=None):
    retriever = MagicMock()
    retriever.name = name
    retriever.retrieve = AsyncMock(return_value=hits or [])
    return retriever


def _planner(plan: QueryPlan):
    planner = MagicMock()
    planner._llm = MagicMock()
    planner.plan = AsyncMock(return_value=plan)
    return planner


def _reranker():
    reranker = MagicMock()
    reranker._llm = MagicMock()
    reranker.rerank = AsyncMock(side_effect=lambda question, hits: hits)
    return reranker


def _agent(pack: EvidencePack):
    agent = MagicMock()
    agent._llm = MagicMock()
    agent.run = AsyncMock(return_value=pack)
    return agent


def _pipeline(retrievers, planner, reranker, agent, fused_top_n=5, top_k=5):
    return HybridRetrievalPipeline(
        retrievers=retrievers,
        planner=planner,
        reranker=reranker,
        agent=agent,
        top_k=top_k,
        fused_top_n=fused_top_n,
        max_iterations=2,
    )


class TestStrategyQuery:
    def test_appends_plan_hints(self):
        plan = QueryPlan(
            sub_queries=["q"], entities=["张三"], events=["大战"], chapter_hints=["第3章"]
        )
        pipeline = _pipeline({}, _planner(plan), _reranker(), _agent(EvidencePack([])))

        assert pipeline._strategy_query(plan, "entity", "q") == "q 张三"
        assert pipeline._strategy_query(plan, "event", "q") == "q 大战"
        assert pipeline._strategy_query(plan, "chapter", "q") == "q 第3章"
        assert pipeline._strategy_query(plan, "dense", "q") == "q"

    def test_no_hints_keeps_query(self):
        plan = QueryPlan(sub_queries=["q"])
        pipeline = _pipeline({}, _planner(plan), _reranker(), _agent(EvidencePack([])))
        assert pipeline._strategy_query(plan, "entity", "q") == "q"


class TestRunHybridQueries:
    @pytest.mark.asyncio
    async def test_fuses_hits_from_all_strategies(self):
        dense = _retriever("dense", [_hit(1, 0)])
        bm25 = _retriever("bm25", [_hit(1, 1)])
        plan = QueryPlan(sub_queries=["q"], strategies=["dense", "bm25"])
        pipeline = _pipeline(
            {"dense": dense, "bm25": bm25}, _planner(plan), _reranker(),
            _agent(EvidencePack([])),
        )

        fused = await pipeline._run_hybrid_queries("q", plan, [1])

        assert {h.chunk_index for h in fused} == {0, 1}
        dense.retrieve.assert_awaited_once()
        bm25.retrieve.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unknown_strategy_skipped(self):
        dense = _retriever("dense", [_hit(1, 0)])
        plan = QueryPlan(sub_queries=["q"], strategies=["dense", "weird"])
        pipeline = _pipeline({"dense": dense}, _planner(plan), _reranker(),
                             _agent(EvidencePack([])))

        fused = await pipeline._run_hybrid_queries("q", plan, [1])
        assert len(fused) == 1

    @pytest.mark.asyncio
    async def test_single_retriever_failure_degrades(self):
        dense = _retriever("dense", [_hit(1, 0)])
        broken = _retriever("bm25")
        broken.retrieve = AsyncMock(side_effect=RuntimeError("bm25 down"))
        plan = QueryPlan(sub_queries=["q"], strategies=["dense", "bm25"])
        pipeline = _pipeline(
            {"dense": dense, "bm25": broken}, _planner(plan), _reranker(),
            _agent(EvidencePack([])),
        )

        fused = await pipeline._run_hybrid_queries("q", plan, [1])
        assert len(fused) == 1  # dense 命中保留，bm25 失败降级为空


class TestRetrieve:
    @pytest.mark.asyncio
    async def test_full_orchestration_order(self):
        """plan → 检索融合 → rerank → agent.run 的完整数据流。"""
        dense = _retriever("dense", [_hit(1, 0, score=0.9)])
        plan = QueryPlan(sub_queries=["q"], strategies=["dense"])
        planner = _planner(plan)
        reranker = _reranker()
        agent = _agent(EvidencePack(hits=[_hit(1, 0)]))
        pipeline = _pipeline({"dense": dense}, planner, reranker, agent)

        pack = await pipeline.retrieve("Q", [1], history=[{"role": "user", "content": "hi"}])

        planner.plan.assert_awaited_once_with("Q", [{"role": "user", "content": "hi"}])
        dense.retrieve.assert_awaited_once_with("q", [1], 5)
        reranker.rerank.assert_awaited_once()
        agent.run.assert_awaited_once()
        assert pack.hits[0].chunk_index == 0

    @pytest.mark.asyncio
    async def test_first_sub_query_hit_stops(self):
        """首个有命中的子查询即停，后续子查询不再检索。"""
        dense = _retriever("dense", [_hit(1, 0)])
        plan = QueryPlan(sub_queries=["q1", "q2"], strategies=["dense"])
        pipeline = _pipeline({"dense": dense}, _planner(plan), _reranker(),
                             _agent(EvidencePack([])))

        await pipeline.retrieve("Q", [1])

        assert dense.retrieve.await_count == 1
        assert dense.retrieve.await_args.args[0] == "q1"

    @pytest.mark.asyncio
    async def test_no_hits_probes_next_sub_query(self):
        """第一个子查询无命中 → 继续第二个子查询。"""
        dense = _retriever("dense", [])
        plan = QueryPlan(sub_queries=["q1", "q2"], strategies=["dense"])
        pipeline = _pipeline({"dense": dense}, _planner(plan), _reranker(),
                             _agent(EvidencePack([])))

        await pipeline.retrieve("Q", [1])

        assert dense.retrieve.await_count == 2
        assert [c.args[0] for c in dense.retrieve.await_args_list] == ["q1", "q2"]
