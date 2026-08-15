"""E2E 测试专用的 LLM Provider。"""
import json
import re
from typing import AsyncGenerator, Dict, List, Optional

E2E_TEST_HEADER = "x-e2e-test"
E2E_MOCK_THINKING_HEADER = "x-e2e-mock-thinking"
# #45：让 mock 在 ``chat`` / ``stream_chat`` 抛 ``RuntimeError``，用于稳定测试
# 运行时 LLM 不可用路径（替代昂贵的真实 provider 失败注入）。
E2E_MOCK_LLM_ERROR_HEADER = "x-e2e-mock-llm-error"
# #66：控制 Evidence Agent 证据判定的确定性行为——取值 ``insufficient``
# 时判定证据不足（触发补充检索循环），其余取值判定足够。
E2E_MOCK_JUDGE_HEADER = "x-e2e-mock-judge"
MOCK_LLM_ANSWER = "Hello! I am a mocked DocQA assistant. (no real LLM was called)"
MOCK_CHUNK_SIZE = 5
MOCK_THINKING_PREFIX = "<think>Mock reasoning about the user's question.</think>"
MOCK_LLM_ERROR_MESSAGE = "Mock LLM unavailable"

# #66：各内部 LLM 任务的 prompt 标记 → 确定性 mock 响应。
MOCK_PLAN_RESPONSE = {
    "sub_queries": ["__QUESTION__"],
    "entities": [],
    "events": [],
    "chapter_hints": [],
    "strategies": ["dense", "bm25", "entity", "event", "chapter"],
}
MOCK_PLAN_QUERIES_RESPONSE = {"queries": ["mock refinement query"]}
MOCK_EXTRACT_EVENTS_RESPONSE = {"events": []}
# #80：图谱三元组抽取的确定性 mock 输出（E2E 可断言图数据）。
MOCK_EXTRACT_TRIPLES_RESPONSE = {
    "triples": [
        {"subject": "张三", "relation": "是", "object": "主角", "chunk": 0},
        {"subject": "李四", "relation": "击败", "object": "张三", "chunk": 0},
    ]
}


class MockLLMProvider:
    """提供稳定、无网络依赖的 E2E 对话响应。

    ``#45`` ``fail_with_error=True`` 时所有调用立即抛 :class:`RuntimeError`，
    用于让 E2E 在不依赖真实 provider 网络错误的前提下验证 "LLM 不可用" UI
    路径。``include_thinking`` 与 ``fail_with_error`` 互不影响；thinking 是
    内容前缀，error 是行为开关。

    #66：planner / judge / plan_more_queries 等内部任务按 prompt 标记返回
    确定性 JSON（sub_queries 回填 prompt 中的原问题，保证 dense 检索行为
    与旧单路一致）；``judge_sufficient=False`` 时判定证据不足。
    """

    def __init__(
        self,
        include_thinking: bool = False,
        fail_with_error: bool = False,
        judge_sufficient: bool = True,
    ) -> None:
        self._include_thinking = include_thinking
        self._fail_with_error = fail_with_error
        self._judge_sufficient = judge_sufficient

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
    ) -> str:
        """``#45`` 注入失败时直接抛 :class:`RuntimeError`；否则按 prompt 标记
        返回确定性响应（无标记时返回固定回答）。"""
        if self._fail_with_error:
            raise RuntimeError(MOCK_LLM_ERROR_MESSAGE)
        content = messages[-1]["content"] if messages else ""
        if content.startswith("[QUERY_PLAN]"):
            return self._mock_plan_response(content)
        if content.startswith("[JUDGE_EVIDENCE]"):
            return "SUFFICIENT" if self._judge_sufficient else "INSUFFICIENT"
        if content.startswith("[PLAN_QUERIES]"):
            return json.dumps(MOCK_PLAN_QUERIES_RESPONSE, ensure_ascii=False)
        if content.startswith("[EXTRACT_EVENTS]"):
            return json.dumps(MOCK_EXTRACT_EVENTS_RESPONSE, ensure_ascii=False)
        if content.startswith("[EXTRACT_TRIPLES]"):
            # #80：返回固定三元组，供 E2E 对图数据表/查询接口做确定性断言。
            return json.dumps(MOCK_EXTRACT_TRIPLES_RESPONSE, ensure_ascii=False)
        if self._include_thinking:
            return MOCK_THINKING_PREFIX + MOCK_LLM_ANSWER
        return MOCK_LLM_ANSWER

    @staticmethod
    def _mock_plan_response(prompt: str) -> str:
        """planner mock：sub_queries 回填 prompt 中的原问题（dense 检索不受影响）。

        #79：strategies 按 prompt 中动态生成的可用策略列表返回（v1 全量 /
        v2 子集均可断言、不越界）；prompt 无可用策略行（旧格式）时回退
        全量五路（向后兼容）。
        """
        question = "mock sub query"
        match = re.search(r"用户问题：\s*(.*)$", prompt)
        if match:
            question = match.group(1).strip()
        response = dict(MOCK_PLAN_RESPONSE)
        response["sub_queries"] = [question]
        available = MockLLMProvider._extract_available_strategies(prompt)
        if available is not None:
            response["strategies"] = available
        return json.dumps(response, ensure_ascii=False)

    @staticmethod
    def _extract_available_strategies(prompt: str) -> Optional[List[str]]:
        """从 planner prompt 的"可用策略列表："行提取策略名列表。

        无该行（旧 prompt 格式）返回 ``None``（调用方回退全量五路）。
        """
        match = re.search(r"可用策略列表：(.+)", prompt)
        if not match:
            return None
        return [s.strip() for s in match.group(1).split(",") if s.strip()]

    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
    ) -> AsyncGenerator[str, None]:
        """``#45`` 注入失败时直接抛 :class:`RuntimeError`，否则按固定分块流式返回。"""
        if self._fail_with_error:
            raise RuntimeError(MOCK_LLM_ERROR_MESSAGE)
        if self._include_thinking:
            yield MOCK_THINKING_PREFIX
        for index in range(0, len(MOCK_LLM_ANSWER), MOCK_CHUNK_SIZE):
            yield MOCK_LLM_ANSWER[index : index + MOCK_CHUNK_SIZE]
