"""#66：混合检索管线编排——Query Planner → 五路检索 → Fusion → Reranker
→ Evidence Pack → Evidence Agent 证据循环。

#71：五路检索与多个子查询均并行执行（asyncio.gather），子查询结果经
:func:`app.services.retrieval.fusion.merge_hits` 去重合并取 top-N。
"""
import asyncio
import logging
import time
from typing import Dict, List, Optional

from app.core.config import get_settings
from app.core.perf import elapsed_ms
from app.services.retrieval import RetrievalHit
from app.services.retrieval.agent import EvidenceAgent
from app.services.retrieval.bm25 import BM25Retriever
from app.services.retrieval.dense import DenseRetriever
from app.services.retrieval.evidence import EvidencePack
from app.services.retrieval.fusion import merge_hits, normalize_scores, rrf_fusion
from app.services.retrieval.metadata import ChapterRetriever, EntityRetriever, EventRetriever
from app.services.retrieval.planner import QueryPlan, QueryPlanner
from app.services.retrieval.reranker import LLMReranker

settings = get_settings()
logger = logging.getLogger(__name__)

# 策略名 → 检索器类（工厂按 settings 开关实例化）。
_RETRIEVER_CLASSES = {
    "dense": DenseRetriever,
    "bm25": BM25Retriever,
    "entity": EntityRetriever,
    "event": EventRetriever,
    "chapter": ChapterRetriever,
}

# 策略名 → settings 开关字段名。
_STRATEGY_SWITCHES = {
    "dense": "retrieval_dense_enabled",
    "bm25": "retrieval_bm25_enabled",
    "entity": "retrieval_entity_enabled",
    "event": "retrieval_event_enabled",
    "chapter": "retrieval_chapter_enabled",
}


def build_retrievers() -> Dict[str, object]:
    """按 settings 开关构建检索器集合（entity/event 索引缺失不影响构建，
    检索器内部会降级为空结果）。"""
    retrievers: Dict[str, object] = {}
    for strategy, cls in _RETRIEVER_CLASSES.items():
        if not getattr(settings, _STRATEGY_SWITCHES[strategy], True):
            continue
        retrievers[strategy] = cls()
    return retrievers


class HybridRetrievalPipeline:
    """混合检索 + 证据循环管线。

    各组件均可注入（单测 mock）；``request`` 透传给 LLM 工厂，让
    X-E2E-Test 头在 E2E 下把 planner/reranker/agent 的 LLM 调用全部切到
    MockLLMProvider。
    """

    def __init__(
        self,
        retrievers: Optional[Dict[str, object]] = None,
        planner: Optional[QueryPlanner] = None,
        reranker: Optional[LLMReranker] = None,
        agent: Optional[EvidenceAgent] = None,
        request=None,
        top_k: Optional[int] = None,
        fused_top_n: Optional[int] = None,
        max_iterations: Optional[int] = None,
    ):
        self._retrievers = retrievers if retrievers is not None else build_retrievers()
        self._planner = planner or QueryPlanner()
        self._reranker = reranker or LLMReranker()
        self._request = request
        self._top_k = top_k if top_k is not None else settings.retrieval_top_k
        self._fused_top_n = (
            fused_top_n if fused_top_n is not None else settings.retrieval_fused_top_n
        )
        max_iterations = (
            max_iterations
            if max_iterations is not None
            else settings.evidence_max_iterations
        )
        if agent is None:
            agent = EvidenceAgent(
                retrieve_fn=self._run_hybrid_queries,
                max_iterations=max_iterations,
            )
        self._agent = agent

    def _resolve_llm(self):
        from app.services.llm import get_llm_provider

        return get_llm_provider(self._request)

    def _llm_planner(self) -> QueryPlanner:
        """planner/reranker/agent 的 LLM 每次调用按 request 工厂解析。"""
        if self._planner._llm is None:
            self._planner._llm = self._resolve_llm()
        return self._planner

    def _llm_reranker(self) -> LLMReranker:
        if self._reranker._llm is None:
            self._reranker._llm = self._resolve_llm()
        return self._reranker

    def _llm_agent(self) -> EvidenceAgent:
        if self._agent._llm is None:
            self._agent._llm = self._resolve_llm()
        return self._agent

    def _strategy_query(self, plan: QueryPlan, strategy: str, query: str) -> str:
        """把 QueryPlan 的实体/事件/章节线索拼进对应策略的检索词。"""
        if strategy == "entity" and plan.entities:
            return f"{query} {' '.join(plan.entities)}"
        if strategy == "event" and plan.events:
            return f"{query} {' '.join(plan.events)}"
        if strategy == "chapter" and plan.chapter_hints:
            return f"{query} {' '.join(plan.chapter_hints)}"
        return query

    async def _safe_retrieve(
        self,
        strategy: str,
        query: str,
        plan: QueryPlan,
        document_ids: List[int],
    ) -> List[RetrievalHit]:
        """单路检索：失败降级为空（并行执行时互不干扰）；耗时按策略打点。"""
        retriever = self._retrievers.get(strategy)
        if retriever is None:
            return []
        start = time.perf_counter()
        try:
            strategy_query = self._strategy_query(plan, strategy, query)
            hits = await retriever.retrieve(
                strategy_query, document_ids or None, self._top_k
            )
        except Exception as exc:  # noqa: BLE001 — 单路失败降级为空
            logger.warning(
                "[perf] strategy=%s failed query=%.40r ms=%.1f exc=%s",
                strategy,
                query,
                elapsed_ms(start),
                exc,
            )
            return []
        logger.info(
            "[perf] strategy=%s query=%.40r hits=%d ms=%.1f",
            strategy,
            query,
            len(hits or []),
            elapsed_ms(start),
        )
        return hits or []

    async def _run_hybrid_queries(
        self,
        query: str,
        plan: QueryPlan,
        document_ids: List[int],
    ) -> List[RetrievalHit]:
        """对单个查询并行执行五路检索 + RRF 融合（不含证据循环）。"""
        start = time.perf_counter()
        strategies = [s for s in plan.strategies if s in self._retrievers]
        results = await asyncio.gather(
            *(self._safe_retrieve(s, query, plan, document_ids) for s in strategies)
        )
        hit_groups = {s: hits for s, hits in zip(strategies, results)}
        fused = rrf_fusion(hit_groups, top_n=self._fused_top_n)
        logger.info(
            "[perf] hybrid query=%.40r strategies=%d fused=%d ms=%.1f",
            query,
            len(strategies),
            len(fused),
            elapsed_ms(start),
        )
        return fused

    async def retrieve(
        self,
        question: str,
        document_ids: List[int],
        history: Optional[List[Dict[str, str]]] = None,
    ) -> EvidencePack:
        """完整问答证据管线：规划 → 检索 → 融合 → 重排 → 证据循环。

        返回最终 EvidencePack；``pack.hits`` 顺序即 RAG prompt 的上下文
        顺序。
        """
        total_start = time.perf_counter()

        # 1. Query Planner：问题 + 历史 → QueryPlan。
        plan_start = time.perf_counter()
        plan = await self._llm_planner().plan(question, history)
        logger.info(
            "[perf] plan sub_queries=%d strategies=%s ms=%.1f",
            len(plan.sub_queries),
            ",".join(plan.strategies),
            elapsed_ms(plan_start),
        )

        # 2. Hybrid Retrieval：子查询并行探测 → 去重合并（#71）。
        if not plan.sub_queries:
            fused: List[RetrievalHit] = []
        else:
            results = await asyncio.gather(
                *(
                    self._run_hybrid_queries(sq, plan, document_ids)
                    for sq in plan.sub_queries
                )
            )
            fused = sorted(
                merge_hits(*results), key=lambda h: h.score, reverse=True
            )[: self._fused_top_n]

        # 3. Reranker：LLM 重排（不可用直通）。
        rerank_start = time.perf_counter()
        reranked = await self._llm_reranker().rerank(question, fused)
        logger.info(
            "[perf] rerank in=%d out=%d ms=%.1f",
            len(fused),
            len(reranked),
            elapsed_ms(rerank_start),
        )

        # 4. Evidence Pack（归一化分数便于 SSE 透出）。
        evidence = normalize_scores(reranked)

        # 5. Evidence Agent：证据循环（足够 → 作答 / 不足 → 补充检索）。
        agent_start = time.perf_counter()
        pack = await self._llm_agent().run(question, plan, document_ids, evidence)
        logger.info(
            "[perf] agent iterations=%d hits=%d sufficient=%s ms=%.1f",
            pack.iterations,
            len(pack.hits),
            pack.sufficient,
            elapsed_ms(agent_start),
        )
        logger.info("[perf] retrieval.total ms=%.1f", elapsed_ms(total_start))
        return pack
