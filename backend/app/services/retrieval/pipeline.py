"""#66：混合检索管线编排——Query Planner → 五路检索 → Fusion → Reranker
→ Evidence Pack → Evidence Agent 证据循环。

#71：五路检索与多个子查询均并行执行（asyncio.gather），子查询结果经
:func:`app.services.retrieval.fusion.merge_hits` 去重合并取 top-N。

#74：检索器集合全部经构造注入（``Dict[str, Retriever]``，key 为各检索器
自描述的 ``strategy`` 名）——管线不 import、不实例化任何具体检索器类，
也不感知 settings 开关（装配逻辑见 :mod:`app.services.retrieval.assembly`）；
QueryPlan 线索由各检索器经可选 ``decorate_query(query, plan)`` 钩子自行消费。

#75：可选 ``strategies`` 白名单——生效集合 = 白名单 ∩ 注入检索器集合 ∩
Query Planner 建议（``None`` 等价于不限定）；证据循环的补充检索复用同一
并行检索入口（:meth:`_run_hybrid_queries`），自动继承白名单约束。

#79：生效检索器对象集合（白名单 ∩ 注入集合的对象子集）在管线内计算一次
（:attr:`_active_retrievers`），并注入默认 planner——并行检索入口与证据
循环补充检索与 planner 共用同一来源（单一事实）；planner 建议 ⊆ 生效集合，
交集为空只可能发生在生效集合本身为空（此时检索结果为空，符合现有语义）。
"""
import asyncio
import logging
import time
from typing import Dict, List, Optional

from app.core.config import get_settings
from app.core.perf import elapsed_ms
from app.services.retrieval import Retriever, RetrievalHit
from app.services.retrieval.agent import EvidenceAgent
from app.services.retrieval.evidence import EvidencePack
from app.services.retrieval.fusion import merge_hits, normalize_scores, rrf_fusion
from app.services.retrieval.planner import QueryPlan, QueryPlanner
from app.services.retrieval.reranker import LLMReranker

settings = get_settings()
logger = logging.getLogger(__name__)


class HybridRetrievalPipeline:
    """混合检索 + 证据循环管线。

    检索器集合由构造注入（依赖 :class:`Retriever` 契约与 ``RetrievalHit``，
    不感知任何具体策略名 / settings 开关）；planner/reranker/agent 也可注入
    （单测 mock）。``request`` 透传给 LLM 工厂，让 X-E2E-Test 头在 E2E 下把
    planner/reranker/agent 的 LLM 调用全部切到 MockLLMProvider。

    ``strategies`` 为调用方检索策略白名单（#75）：生效集合 = 白名单 ∩
    注入检索器集合（#79 起为对象子集 :attr:`_active_retrievers`，并注入
    默认 planner）；``None`` 等价于不限定（现有行为）。
    """

    def __init__(
        self,
        retrievers: Dict[str, Retriever],
        planner: Optional[QueryPlanner] = None,
        reranker: Optional[LLMReranker] = None,
        agent: Optional[EvidenceAgent] = None,
        request=None,
        top_k: Optional[int] = None,
        fused_top_n: Optional[int] = None,
        max_iterations: Optional[int] = None,
        strategies: Optional[List[str]] = None,
    ):
        self._retrievers = retrievers
        # #75：调用方白名单（``None`` 不限定）；与注入集合 / Planner 建议
        # 取交集，证据循环补充检索复用 ``_run_hybrid_queries`` 自动继承。
        self._strategies = set(strategies) if strategies is not None else None
        # #79：生效检索器对象集合 = 白名单 ∩ 注入集合（单一来源）——注入
        # 默认 planner，并行检索 / 证据循环共用；``None`` 白名单等价不限定。
        self._active_retrievers = self._compute_active_retrievers()
        self._planner = planner or QueryPlanner(retrievers=self._active_retrievers)
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

    def _compute_active_retrievers(self) -> Dict[str, Retriever]:
        """#79：生效检索器对象集合 = 白名单 ∩ 注入集合（对象子集）。

        planner 注入 / 并行检索 / 证据循环补充检索共用本集合（单一来源）；
        白名单为 ``None`` 时等价于注入全集。
        """
        if self._strategies is None:
            return dict(self._retrievers)
        return {
            s: r for s, r in self._retrievers.items() if s in self._strategies
        }

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
            # #74：QueryPlan 线索由检索器经可选 decorate_query 钩子自行消费，
            # 管线不感知任何具体策略名；未实现钩子的检索器透传原始 query。
            decorate = getattr(retriever, "decorate_query", None)
            strategy_query = decorate(query, plan) if callable(decorate) else query
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
        """对单个查询并行执行检索 + RRF 融合（不含证据循环）。

        #75/#79：生效策略取自 :attr:`_active_retrievers`（白名单 ∩ 注入集
        合，与 planner 共用单一来源）；planner 建议越界（mock 场景）仍被
        过滤。``None`` 白名单等价于不限定。证据循环补充检索同样经本入口。
        """
        start = time.perf_counter()
        strategies = [s for s in plan.strategies if s in self._active_retrievers]
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
