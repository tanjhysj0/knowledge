"""#66：Query Planner 单测（解析纯函数 + LLM 驱动的规划与降级）。

#79：注入检索器对象集合后——prompt 动态生成可用策略列表、解析与降级
# 均按可用集合过滤；未注入时行为与 #66 完全一致（全量六路）。
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.retrieval.planner import (
    ALL_STRATEGIES,
    AVAILABLE_STRATEGIES_LINE,
    PLANNER_MARKER,
    QueryPlan,
    QueryPlanner,
    parse_query_plan,
)


def _retrievers(*strategies: str):
    """按 strategy 名构造最小检索器对象（仅暴露自描述的 strategy 属性）。"""
    return {s: MagicMock(strategy=s) for s in strategies}


class TestParseQueryPlan:
    def test_parses_full_plan(self):
        raw = (
            '{"sub_queries": ["q1", "q2"], "entities": ["张三"], '
            '"events": ["大战"], "chapter_hints": ["第3章"], '
            '"strategies": ["dense", "bm25", "entity"]}'
        )
        plan = parse_query_plan(raw, "fallback")
        assert plan.sub_queries == ["q1", "q2"]
        assert plan.entities == ["张三"]
        assert plan.events == ["大战"]
        assert plan.chapter_hints == ["第3章"]
        assert plan.strategies == ["dense", "bm25", "entity"]

    def test_filters_unknown_strategies(self):
        raw = '{"sub_queries": ["q"], "strategies": ["dense", "weird"]}'
        plan = parse_query_plan(raw, "fallback")
        assert plan.strategies == ["dense"]

    def test_empty_strategies_fallback_to_all(self):
        raw = '{"sub_queries": ["q"], "strategies": []}'
        plan = parse_query_plan(raw, "fallback")
        assert plan.strategies == ALL_STRATEGIES

    def test_empty_sub_queries_fallback_to_question(self):
        raw = '{"sub_queries": [], "strategies": ["dense"]}'
        plan = parse_query_plan(raw, "fallback")
        assert plan.sub_queries == ["fallback"]

    def test_invalid_json_falls_back(self):
        plan = parse_query_plan("not json at all", "fallback")
        assert plan.sub_queries == ["fallback"]
        assert plan.strategies == ALL_STRATEGIES

    def test_empty_raw_falls_back(self):
        plan = parse_query_plan("", "fallback")
        assert plan.sub_queries == ["fallback"]

    def test_tolerates_markdown_wrapper(self):
        raw = '```json\n{"sub_queries": ["q"]}\n```'
        plan = parse_query_plan(raw, "fallback")
        assert plan.sub_queries == ["q"]

    def test_filters_non_string_items(self):
        """LLM 输出混合类型时只保留非空字符串，避免下游 join 报错。"""
        raw = (
            '{"sub_queries": ["ok", 3, ""], "entities": ["张三", 123, null], '
            '"events": [{"nested": 1}], "chapter_hints": ["第3章", 5]}'
        )
        plan = parse_query_plan(raw, "fallback")
        assert plan.sub_queries == ["ok"]
        assert plan.entities == ["张三"]
        assert plan.events == []
        assert plan.chapter_hints == ["第3章"]

    def test_non_list_hints_become_empty(self):
        raw = '{"sub_queries": ["q"], "entities": "not-a-list"}'
        plan = parse_query_plan(raw, "fallback")
        assert plan.entities == []


class TestParseQueryPlanAvailableStrategies:
    """#79：可用策略集合注入——越界策略过滤、空/非法回落可用集合。"""

    def test_filters_out_of_scope_strategies(self):
        raw = '{"sub_queries": ["q"], "strategies": ["dense", "entity"]}'
        plan = parse_query_plan(raw, "fallback", available_strategies=["dense", "bm25"])
        assert plan.strategies == ["dense"]

    def test_empty_strategies_fallback_to_available(self):
        raw = '{"sub_queries": ["q"], "strategies": []}'
        plan = parse_query_plan(raw, "fallback", available_strategies=["dense", "bm25"])
        assert plan.strategies == ["dense", "bm25"]

    def test_all_out_of_scope_fallback_to_available(self):
        """LLM 建议全部越界（或策略键缺失）→ 回落可用集合而非全量。"""
        raw = '{"sub_queries": ["q"], "strategies": ["entity", "chapter"]}'
        plan = parse_query_plan(raw, "fallback", available_strategies=["dense"])
        assert plan.strategies == ["dense"]

    def test_invalid_json_falls_back_to_available(self):
        plan = parse_query_plan("garbage", "fallback", available_strategies=["bm25"])
        assert plan.sub_queries == ["fallback"]
        assert plan.strategies == ["bm25"]

    def test_empty_raw_falls_back_to_available(self):
        plan = parse_query_plan("", "fallback", available_strategies=["dense", "bm25"])
        assert plan.strategies == ["dense", "bm25"]

    def test_none_available_keeps_legacy_full_set(self):
        """未指定可用集合时行为与 #66 完全一致（全量六路）。"""
        raw = '{"sub_queries": ["q"], "strategies": ["entity"]}'
        plan = parse_query_plan(raw, "fallback")
        assert plan.strategies == ["entity"]

    def test_empty_available_yields_empty_plan(self):
        """可用集合为空（生效集合本身为空）→ 计划策略为空列表。"""
        raw = '{"sub_queries": ["q"], "strategies": ["dense"]}'
        plan = parse_query_plan(raw, "fallback", available_strategies=[])
        assert plan.strategies == []


class TestQueryPlanner:
    @pytest.mark.asyncio
    async def test_plan_calls_llm_with_marker_and_question(self):
        llm = MagicMock()
        llm.chat = AsyncMock(return_value='{"sub_queries": ["q1"], "strategies": ["dense"]}')
        planner = QueryPlanner(llm=llm)

        plan = await planner.plan("主角是谁？", history=[{"role": "user", "content": "你好"}])

        assert plan.sub_queries == ["q1"]
        prompt = llm.chat.call_args.kwargs["messages"][0]["content"]
        assert prompt.startswith(PLANNER_MARKER)
        assert "主角是谁？" in prompt
        assert "你好" in prompt  # 历史进入 prompt

    @pytest.mark.asyncio
    async def test_plan_llm_failure_falls_back(self):
        llm = MagicMock()
        llm.chat = AsyncMock(side_effect=RuntimeError("llm down"))
        planner = QueryPlanner(llm=llm)

        plan = await planner.plan("Q", history=[])
        assert plan.sub_queries == ["Q"]
        assert plan.strategies == ALL_STRATEGIES

    @pytest.mark.asyncio
    async def test_plan_uses_injected_llm_without_factory(self):
        llm = MagicMock()
        llm.chat = AsyncMock(return_value="garbage")
        planner = QueryPlanner(llm=llm)
        plan = await planner.plan("Q")
        # 解析失败 → 保守计划（且不触碰 get_llm_provider 工厂）
        assert plan.sub_queries == ["Q"]

    def test_query_plan_to_dict(self):
        plan = QueryPlan(sub_queries=["a"], entities=["b"])
        assert plan.to_dict()["sub_queries"] == ["a"]
        assert plan.to_dict()["entities"] == ["b"]


class TestQueryPlannerInjectedRetrievers:
    """#79：注入生效检索器对象集合——动态 prompt / 越界过滤 / 降级限制。"""

    @pytest.mark.asyncio
    async def test_prompt_contains_dynamic_available_strategies(self):
        """prompt 的可用策略列表由注入集合的自描述 strategy 名动态生成。"""
        llm = MagicMock()
        llm.chat = AsyncMock(return_value='{"sub_queries": ["q1"]}')
        planner = QueryPlanner(llm=llm, retrievers=_retrievers("dense", "bm25"))

        await planner.plan("主角是谁？")

        prompt = llm.chat.call_args.kwargs["messages"][0]["content"]
        # 可用策略列表行由注入集合动态生成，未注入策略不出现
        line = next(
            l for l in prompt.splitlines() if l.startswith(AVAILABLE_STRATEGIES_LINE)
        )
        assert line == AVAILABLE_STRATEGIES_LINE + "dense, bm25"
        assert "可用策略列表：" + ", ".join(ALL_STRATEGIES) not in prompt

    @pytest.mark.asyncio
    async def test_plan_filters_out_of_scope_strategies(self):
        """LLM 建议越界策略 → 解析层过滤，plan.strategies ⊆ 可用集合。"""
        llm = MagicMock()
        llm.chat = AsyncMock(
            return_value='{"sub_queries": ["q1"], "strategies": ["dense", "entity"]}'
        )
        planner = QueryPlanner(llm=llm, retrievers=_retrievers("dense", "bm25"))

        plan = await planner.plan("Q")

        assert plan.strategies == ["dense"]

    @pytest.mark.asyncio
    async def test_plan_failure_falls_back_to_available(self):
        """LLM 不可用 → 降级计划 strategies ⊆ 可用集合（而非全量六路）。"""
        llm = MagicMock()
        llm.chat = AsyncMock(side_effect=RuntimeError("llm down"))
        planner = QueryPlanner(llm=llm, retrievers=_retrievers("dense", "bm25"))

        plan = await planner.plan("Q")

        assert plan.sub_queries == ["Q"]
        assert plan.strategies == ["dense", "bm25"]

    @pytest.mark.asyncio
    async def test_empty_retrievers_yield_empty_available(self):
        """注入空集合（生效集合本身为空）→ prompt 可用列表为空、降级为空策略。"""
        llm = MagicMock()
        llm.chat = AsyncMock(return_value='{"sub_queries": ["q1"]}')
        planner = QueryPlanner(llm=llm, retrievers={})

        plan = await planner.plan("Q")

        prompt = llm.chat.call_args.kwargs["messages"][0]["content"]
        assert AVAILABLE_STRATEGIES_LINE in prompt
        assert plan.strategies == []

    @pytest.mark.asyncio
    async def test_no_retrievers_keeps_legacy_full_prompt(self):
        """未注入 → prompt 可用列表为全量六路（#66 行为不回归）。"""
        llm = MagicMock()
        llm.chat = AsyncMock(return_value='{"sub_queries": ["q1"]}')
        planner = QueryPlanner(llm=llm)

        await planner.plan("Q")

        prompt = llm.chat.call_args.kwargs["messages"][0]["content"]
        assert (
            AVAILABLE_STRATEGIES_LINE + ", ".join(ALL_STRATEGIES) in prompt
        )
