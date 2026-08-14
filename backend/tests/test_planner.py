"""#66：Query Planner 单测（解析纯函数 + LLM 驱动的规划与降级）。"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.retrieval.planner import (
    ALL_STRATEGIES,
    PLANNER_MARKER,
    QueryPlan,
    QueryPlanner,
    parse_query_plan,
)


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
