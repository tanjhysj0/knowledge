"""E2E 测试专用的 LLM Provider。"""

from typing import AsyncGenerator, Dict, List

E2E_TEST_HEADER = "x-e2e-test"
E2E_MOCK_THINKING_HEADER = "x-e2e-mock-thinking"
# #45：让 mock 在 ``chat`` / ``stream_chat`` 抛 ``RuntimeError``，用于稳定测试
# 运行时 LLM 不可用路径（替代昂贵的真实 provider 失败注入）。
E2E_MOCK_LLM_ERROR_HEADER = "x-e2e-mock-llm-error"
MOCK_LLM_ANSWER = "Hello! I am a mocked DocQA assistant. (no real LLM was called)"
MOCK_CHUNK_SIZE = 5
MOCK_THINKING_PREFIX = "<think>Mock reasoning about the user's question.</think>"
MOCK_LLM_ERROR_MESSAGE = "Mock LLM unavailable"


class MockLLMProvider:
    """提供稳定、无网络依赖的 E2E 对话响应。

    ``#45`` ``fail_with_error=True`` 时所有调用立即抛 :class:`RuntimeError`，
    用于让 E2E 在不依赖真实 provider 网络错误的前提下验证 "LLM 不可用" UI
    路径。``include_thinking`` 与 ``fail_with_error`` 互不影响；thinking 是
    内容前缀，error 是行为开关。
    """

    def __init__(
        self,
        include_thinking: bool = False,
        fail_with_error: bool = False,
    ) -> None:
        self._include_thinking = include_thinking
        self._fail_with_error = fail_with_error

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
    ) -> str:
        """``#45`` 注入失败时直接抛 :class:`RuntimeError`，否则返回固定回答。"""
        if self._fail_with_error:
            raise RuntimeError(MOCK_LLM_ERROR_MESSAGE)
        if self._include_thinking:
            return MOCK_THINKING_PREFIX + MOCK_LLM_ANSWER
        return MOCK_LLM_ANSWER

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