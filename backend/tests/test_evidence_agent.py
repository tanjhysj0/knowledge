"""#66：Evidence Agent（LangGraph 证据循环）单测。

全部注入 mock LLM + mock 检索器，覆盖 PRD 要求的三条分支：

1. 证据足够 → 直接作答（无补充检索）
2. 证据不足 → 补充检索 → 证据足够 → 作答
3. 证据不足 → 补充检索 → 仍不足 → 达到迭代上限 → 强制作答（带提示）
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.retrieval import RetrievalHit
from app.services.retrieval.agent import (
    LIMITED_EVIDENCE_NOTE,
    EvidenceAgent,
    merge_evidence,
    parse_judge_result,
    parse_plan_queries_result,
)
from app.services.retrieval.planner import QueryPlan


def _hit(doc_id: int, chunk: int, content: str = "evidence text", score: float = 0.8) -> RetrievalHit:
    return RetrievalHit(
        document_id=doc_id, chunk_index=chunk, content=content,
        score=score, strategy="dense",
    )


def _judge_llm(responses: list) -> MagicMock:
    """按调用顺序返回指定判定文本的 mock LLM。"""
    llm = MagicMock()
    llm.chat = AsyncMock(side_effect=responses)
    return llm


def _prompt_aware_llm(
    judge_responses, plan_response: str = '{"queries": ["refined query"]}'
) -> MagicMock:
    """按 prompt 标记返回响应：judge 按调用序消费 ``judge_responses``，
    plan_more_queries 固定返回 ``plan_response``。"""
    llm = MagicMock()
    judge_queue = list(judge_responses)

    async def chat(messages, temperature=0.7):
        content = messages[-1]["content"]
        if content.startswith("[JUDGE_EVIDENCE]"):
            if not judge_queue:
                raise RuntimeError("no more judge responses")
            return judge_queue.pop(0)
        if content.startswith("[PLAN_QUERIES]"):
            return plan_response
        raise RuntimeError("unexpected prompt: " + content[:40])

    llm.chat = chat
    return llm


class TestParseJudgeResult:
    def test_sufficient(self):
        assert parse_judge_result("SUFFICIENT") is True

    def test_insufficient(self):
        assert parse_judge_result("INSUFFICIENT") is False

    def test_json_form(self):
        assert parse_judge_result('{"sufficient": true}') is True
        assert parse_judge_result('{"sufficient": false}') is False

    def test_unrecognized_returns_none(self):
        assert parse_judge_result("hello world") is None
        assert parse_judge_result("") is None
        assert parse_judge_result(None) is None

    def test_insufficient_wins_over_sufficient_substring(self):
        assert parse_judge_result("NOT SUFFICIENT BUT INSUFFICIENT") is False


class TestParsePlanQueriesResult:
    def test_parses_queries(self):
        assert parse_plan_queries_result('{"queries": ["a", "b"]}') == ["a", "b"]

    def test_invalid_returns_empty(self):
        assert parse_plan_queries_result("") == []
        assert parse_plan_queries_result("not json") == []
        assert parse_plan_queries_result('{"other": 1}') == []

    def test_filters_non_string_queries(self):
        assert parse_plan_queries_result('{"queries": ["ok", 3, ""]}') == ["ok"]


class TestMergeEvidence:
    def test_dedupes_by_document_and_chunk(self):
        existing = [_hit(1, 0)]
        incoming = [_hit(1, 0), _hit(2, 0)]
        merged = merge_evidence(existing, incoming)
        keys = {(h.document_id, h.chunk_index) for h in merged}
        assert keys == {(1, 0), (2, 0)}

    def test_keeps_higher_score_on_duplicate(self):
        existing = [_hit(1, 0, score=0.9)]
        incoming = [_hit(1, 0, score=0.5)]
        merged = merge_evidence(existing, incoming)
        assert merged[0].score == 0.9


class TestEvidenceAgentBranches:
    """三条分支：足够 / 不足→补充→足够 / 达到上限强制作答。"""

    def _agent(self, judge_responses, retrieve_hits, max_iterations=2):
        llm = _judge_llm(judge_responses)
        retrieve_fn = AsyncMock(return_value=retrieve_hits)
        agent = EvidenceAgent(
            llm=llm, retrieve_fn=retrieve_fn, max_iterations=max_iterations
        )
        return agent, retrieve_fn

    @pytest.mark.asyncio
    async def test_sufficient_evidence_answers_directly(self):
        agent, retrieve_fn = self._agent(
            judge_responses=["SUFFICIENT"], retrieve_hits=[]
        )
        evidence = [_hit(1, 0)]
        pack = await agent.run("Q", QueryPlan(sub_queries=["Q"]), [1], evidence)

        assert pack.sufficient is True
        assert pack.hits == evidence
        assert pack.iterations == 0
        assert pack.note == ""
        retrieve_fn.assert_not_awaited()  # 足够 → 无补充检索

    @pytest.mark.asyncio
    async def test_insufficient_then_retrieve_then_sufficient(self):
        refined_hit = _hit(2, 0, content="refined evidence")
        llm = _prompt_aware_llm(judge_responses=["INSUFFICIENT", "SUFFICIENT"])
        retrieve_fn = AsyncMock(return_value=[refined_hit])
        agent = EvidenceAgent(llm=llm, retrieve_fn=retrieve_fn, max_iterations=2)

        initial = [_hit(1, 0)]
        pack = await agent.run("Q", QueryPlan(sub_queries=["Q"]), [1], initial)

        retrieve_fn.assert_awaited_once()
        # 补充检索的查询来自 plan_more_queries
        assert len(pack.hits) == 2
        assert pack.iterations == 1
        assert pack.sufficient is True
        assert pack.note == ""

    @pytest.mark.asyncio
    async def test_hits_max_iterations_forces_answer_with_note(self):
        llm = _prompt_aware_llm(judge_responses=["INSUFFICIENT", "INSUFFICIENT"])
        retrieve_fn = AsyncMock(return_value=[_hit(3, 0)])
        agent = EvidenceAgent(llm=llm, retrieve_fn=retrieve_fn, max_iterations=2)

        initial = [_hit(1, 0)]
        pack = await agent.run("Q", QueryPlan(sub_queries=["Q"]), [1], initial)

        # 2 轮 judge：第 1 轮不足 → 补检；第 2 轮不足且 iteration>=2 → 强制作答
        assert retrieve_fn.await_count == 1
        assert pack.sufficient is False
        assert pack.note == LIMITED_EVIDENCE_NOTE
        assert pack.iterations == 1  # 只完成 1 轮补充检索

    @pytest.mark.asyncio
    async def test_empty_evidence_judged_insufficient_without_llm(self):
        """空证据无需 LLM 判定：直接 insufficient → 走补充检索。"""
        llm = MagicMock()
        llm.chat = AsyncMock()
        retrieve_fn = AsyncMock(return_value=[_hit(1, 0)])
        agent = EvidenceAgent(llm=llm, retrieve_fn=retrieve_fn, max_iterations=1)
        pack = await agent.run("Q", QueryPlan(sub_queries=["Q"]), [], [])

        llm.chat.assert_not_awaited()  # judge 不调 LLM
        assert pack.sufficient is False
        assert pack.note == LIMITED_EVIDENCE_NOTE

    @pytest.mark.asyncio
    async def test_judge_llm_failure_fails_open(self):
        """judge LLM 异常 → fail-open（视为足够，直接作答）。"""
        llm = MagicMock()
        llm.chat = AsyncMock(side_effect=RuntimeError("llm down"))
        agent = EvidenceAgent(llm=llm, retrieve_fn=AsyncMock(), max_iterations=2)
        pack = await agent.run("Q", QueryPlan(sub_queries=["Q"]), [1], [_hit(1, 0)])
        assert pack.sufficient is True
        assert pack.iterations == 0

    @pytest.mark.asyncio
    async def test_plan_failure_forces_answer(self):
        """补充查询生成失败 → 直接作答（不无限循环）。"""
        llm = _judge_llm(["INSUFFICIENT", RuntimeError("plan down")])
        agent = EvidenceAgent(llm=llm, retrieve_fn=AsyncMock(), max_iterations=2)
        pack = await agent.run("Q", QueryPlan(sub_queries=["Q"]), [1], [_hit(1, 0)])
        assert pack.sufficient is False
        assert pack.note == LIMITED_EVIDENCE_NOTE

    @pytest.mark.asyncio
    async def test_retrieve_failure_skipped(self):
        """补充检索抛异常 → 跳过，下一轮 judge 继续。"""
        llm = _prompt_aware_llm(judge_responses=["INSUFFICIENT", "INSUFFICIENT"])
        retrieve_fn = AsyncMock(side_effect=RuntimeError("retrieve down"))
        agent = EvidenceAgent(llm=llm, retrieve_fn=retrieve_fn, max_iterations=2)
        pack = await agent.run("Q", QueryPlan(sub_queries=["Q"]), [1], [_hit(1, 0)])
        assert pack.sufficient is False  # 补检失败后 judge 仍不足 → 上限强制作答
        assert len(pack.hits) == 1  # 原证据保留

    @pytest.mark.asyncio
    async def test_supplementary_hits_normalized_before_merge(self):
        """补充命中是原始 RRF 分数（~0.016），归一化后高分替换才公平。"""
        llm = _prompt_aware_llm(judge_responses=["INSUFFICIENT", "SUFFICIENT"])
        # 同 chunk 的补充命中：原始分 0.016（归一化后 1.0）应替换初始分 0.5
        refined_hit = _hit(1, 0, content="refined", score=0.016)
        retrieve_fn = AsyncMock(return_value=[refined_hit])
        agent = EvidenceAgent(llm=llm, retrieve_fn=retrieve_fn, max_iterations=2)

        initial = [_hit(1, 0, score=0.5), _hit(2, 0, score=1.0)]
        pack = await agent.run("Q", QueryPlan(sub_queries=["Q"]), [1], initial)

        merged = {(h.document_id, h.chunk_index): h for h in pack.hits}
        assert merged[(1, 0)].content == "refined"  # 归一化后 1.0 > 0.5，替换成功
        # 最终证据分数统一归一化在 [0, 1]
        assert all(0 <= h.score <= 1 for h in pack.hits)
        assert pack.hits[0].score == 1.0  # 最高分归一化为 1.0
