"""#66：Evidence Agent——LangGraph 证据循环状态机。

状态机（PRD 设计图）：

.. code-block::

    judge: iteration += 1
           sufficient? → answer（END）
           else if iteration >= max_iterations → answer（标注证据有限）
           else → plan_more_queries → retrieve → judge

LLM 与检索函数均由构造注入（单测用 mock LLM + mock 检索器驱动三条
分支）；图自身无外部依赖。
"""
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from app.core.perf import elapsed_ms
from app.services.retrieval import RetrievalHit
from app.services.retrieval.evidence import EvidencePack
from app.services.retrieval.fusion import normalize_scores
from app.services.retrieval.planner import QueryPlan

# prompt 任务标记：MockLLMProvider 靠它们返回确定性的 E2E 判定。
JUDGE_MARKER = "[JUDGE_EVIDENCE]"
PLAN_QUERIES_MARKER = "[PLAN_QUERIES]"

# 达到迭代上限仍证据不足时附加的回答提示。
LIMITED_EVIDENCE_NOTE = "（证据有限：检索补充已达上限，以下回答可能不完整）"

logger = logging.getLogger(__name__)


class AgentState(TypedDict, total=False):
    question: str
    query_plan: Dict[str, Any]
    document_ids: List[int]
    evidence: List[RetrievalHit]
    iteration: int
    sufficient: bool
    note: str
    more_queries: List[str]


def parse_judge_result(raw: str) -> Optional[bool]:
    """解析证据判定 LLM 输出；无法识别返回 ``None``（调用方 fail-open）。

    先尝试 JSON 解析（``{"sufficient": true/false}``），再回退关键词
    SUFFICIENT / INSUFFICIENT（先查 INSUFFICIENT，避免 JSON 键名
    ``"sufficient"`` 造成子串误判）。
    """
    if not raw:
        return None
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            payload = json.loads(match.group(0))
            if isinstance(payload.get("sufficient"), bool):
                return payload["sufficient"]
        except (json.JSONDecodeError, TypeError):
            pass
    cleaned = raw.strip().upper()
    if "INSUFFICIENT" in cleaned:
        return False
    if "SUFFICIENT" in cleaned:
        return True
    return None


def parse_plan_queries_result(raw: str) -> List[str]:
    """解析补充查询 LLM 输出；非法输入返回空列表。"""
    if not raw:
        return []
    match = re.search(r"\{.*\}", raw.strip(), re.DOTALL)
    if not match:
        return []
    try:
        payload = json.loads(match.group(0))
    except (json.JSONDecodeError, TypeError):
        return []
    queries = payload.get("queries")
    if not isinstance(queries, list):
        return []
    return [q for q in queries if isinstance(q, str) and q.strip()]


def merge_evidence(
    existing: List[RetrievalHit], incoming: List[RetrievalHit]
) -> List[RetrievalHit]:
    """按 (document_id, chunk_index) 合并去重，保留高分命中。纯函数。"""
    merged: Dict[tuple, RetrievalHit] = {_key(h): h for h in existing}
    for hit in incoming:
        key = _key(hit)
        current = merged.get(key)
        if current is None or hit.score > current.score:
            merged[key] = hit
        elif hit.chapter and not current.chapter:
            current.chapter = hit.chapter
    return list(merged.values())


def _key(hit: RetrievalHit) -> tuple:
    return (hit.document_id, hit.chunk_index)


class EvidenceAgent:
    """证据循环代理：judge → plan_more_queries → retrieve 循环。

    ``llm`` 为注入的 LLM provider（默认经 :func:`get_llm_provider` 工厂
    解析）；``retrieve_fn`` 为
    ``async (query, query_plan, document_ids) -> hits`` 的检索管线入口
    （补充检索时调用）；``max_iterations`` 为补充检索轮次上限。
    """

    def __init__(
        self,
        llm=None,
        retrieve_fn=None,
        max_iterations: int = 2,
    ):
        self._llm = llm
        self._retrieve_fn = retrieve_fn
        self._max_iterations = max_iterations
        self._graph = self._build_graph()

    def _resolve_llm(self):
        if self._llm is not None:
            return self._llm
        from app.services.llm import get_llm_provider

        return get_llm_provider(None)

    # ------------------------------------------------------------------
    # 图节点
    # ------------------------------------------------------------------

    async def _judge(self, state: AgentState) -> Dict[str, Any]:
        iteration = state.get("iteration", 0) + 1
        evidence = state.get("evidence") or []
        note = state.get("note") or ""

        if not evidence:
            # 空证据无需 LLM 判断：直接判定不足。
            return {"iteration": iteration, "sufficient": False, "note": note}

        try:
            judge_start = time.perf_counter()
            raw = await self._resolve_llm().chat(
                messages=[
                    {
                        "role": "user",
                        "content": self._build_judge_prompt(
                            state["question"], state.get("query_plan") or {}, evidence
                        ),
                    }
                ],
                temperature=0.0,
            )
            logger.info("[perf] agent.judge_llm ms=%.1f", elapsed_ms(judge_start))
        except Exception:  # noqa: BLE001 — 判定失败 fail-open：直接作答
            return {"iteration": iteration, "sufficient": True, "note": note}

        sufficient = parse_judge_result(raw)
        if sufficient is None:
            sufficient = True  # 无法解析判定输出 → fail-open
        return {"iteration": iteration, "sufficient": sufficient, "note": note}

    async def _plan_more_queries(self, state: AgentState) -> Dict[str, Any]:
        try:
            plan_start = time.perf_counter()
            raw = await self._resolve_llm().chat(
                messages=[
                    {
                        "role": "user",
                        "content": self._build_plan_queries_prompt(
                            state["question"],
                            state.get("query_plan") or {},
                            state.get("evidence") or [],
                        ),
                    }
                ],
                temperature=0.3,
            )
            logger.info("[perf] agent.plan_queries_llm ms=%.1f", elapsed_ms(plan_start))
        except Exception:  # noqa: BLE001 — 无法生成补充查询 → 强制作答
            return {"more_queries": [], "note": state.get("note") or ""}
        return {"more_queries": parse_plan_queries_result(raw), "note": state.get("note") or ""}

    async def _retrieve(self, state: AgentState) -> Dict[str, Any]:
        more = state.get("more_queries") or []
        existing = state.get("evidence") or []
        document_ids = state.get("document_ids") or []
        query_plan = QueryPlan(**state.get("query_plan") or {})
        for query in more:
            try:
                hits = await self._retrieve_fn(query, query_plan, document_ids)
            except Exception:  # noqa: BLE001 — 补充检索失败跳过本轮
                continue
            # 补充命中是原始 RRF 分数（量级 ~0.016），与归一化后的初始
            # 证据尺度不同；merge 前先归一化，保证高分替换比较公平。
            existing = merge_evidence(existing, normalize_scores(hits or []))
        return {"evidence": existing}

    # ------------------------------------------------------------------
    # 路由
    # ------------------------------------------------------------------

    def _route_after_judge(self, state: AgentState) -> str:
        if state.get("sufficient"):
            return "answer"
        if state.get("iteration", 0) >= self._max_iterations:
            return "answer"
        return "plan"

    def _route_after_plan(self, state: AgentState) -> str:
        if state.get("more_queries"):
            return "retrieve"
        return "answer"

    # ------------------------------------------------------------------
    # 图构建与执行
    # ------------------------------------------------------------------

    def _build_graph(self):
        builder = StateGraph(AgentState)
        builder.add_node("judge", self._judge)
        builder.add_node("plan_more_queries", self._plan_more_queries)
        builder.add_node("retrieve", self._retrieve)
        builder.add_edge(START, "judge")
        builder.add_conditional_edges(
            "judge",
            self._route_after_judge,
            {"answer": END, "plan": "plan_more_queries"},
        )
        builder.add_conditional_edges(
            "plan_more_queries",
            self._route_after_plan,
            {"answer": END, "retrieve": "retrieve"},
        )
        builder.add_edge("retrieve", "judge")
        return builder.compile()

    async def run(
        self,
        question: str,
        query_plan: QueryPlan,
        document_ids: List[int],
        initial_evidence: List[RetrievalHit],
    ) -> EvidencePack:
        """执行证据循环，返回最终证据包。

        循环结束条件：证据足够 / 迭代达到上限 / 无法生成补充查询。
        达到上限仍不足时 ``note`` 携带证据有限提示（拼入回答 prompt）。
        """
        final = await self._graph.ainvoke(
            {
                "question": question,
                "query_plan": query_plan.to_dict(),
                "document_ids": document_ids,
                "evidence": list(initial_evidence),
                "iteration": 0,
                "sufficient": True,
                "note": "",
                "more_queries": [],
            }
        )
        sufficient = bool(final.get("sufficient"))
        note = final.get("note") or ""
        if not sufficient and not note:
            note = LIMITED_EVIDENCE_NOTE
        # 最终证据统一归一化（多轮补充合并后尺度保持 [0, 1]），保证
        # SSE evidence / done.evidence 透出的 score 与排序语义一致。
        evidence = normalize_scores(final.get("evidence") or [])
        return EvidencePack(
            hits=evidence,
            sufficient=sufficient,
            iterations=final.get("iteration", 0) - 1,  # 首轮 judge 不算补充检索
            note=note,
        )

    # ------------------------------------------------------------------
    # prompt 构造
    # ------------------------------------------------------------------

    def _build_judge_prompt(
        self, question: str, query_plan: Dict[str, Any], evidence: List[RetrievalHit]
    ) -> str:
        evidence_text = "\n\n".join(
            f"[{i}] ({h.strategy}) {h.content[:200]}" for i, h in enumerate(evidence)
        )
        return f"""{JUDGE_MARKER}
判断以下检索证据是否足以回答用户问题。只输出 SUFFICIENT 或 INSUFFICIENT：

用户问题：{question}
检索计划：{json.dumps(query_plan, ensure_ascii=False)}

证据片段：
{evidence_text}"""

    def _build_plan_queries_prompt(
        self, question: str, query_plan: Dict[str, Any], evidence: List[RetrievalHit]
    ) -> str:
        evidence_text = "\n\n".join(
            f"[{i}] ({h.strategy}) {h.content[:150]}" for i, h in enumerate(evidence[:5])
        )
        return f"""{PLAN_QUERIES_MARKER}
现有证据不足以回答用户问题。生成 1-3 个补充检索查询，输出 JSON（不要输出其他内容）：

{{"queries": ["补充查询1", "补充查询2"]}}

用户问题：{question}
检索计划：{json.dumps(query_plan, ensure_ascii=False)}
现有证据：
{evidence_text}"""
