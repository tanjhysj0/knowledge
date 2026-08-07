"""E2E 测试专用的 LLM Provider。"""

from typing import AsyncGenerator, Dict, List

E2E_TEST_HEADER = "x-e2e-test"
E2E_MOCK_THINKING_HEADER = "x-e2e-mock-thinking"
MOCK_LLM_ANSWER = "Hello! I am a mocked DocQA assistant. (no real LLM was called)"
MOCK_CHUNK_SIZE = 5
MOCK_THINKING_PREFIX = "<think>Mock reasoning about the user's question.</think>"


class MockLLMProvider:
    """提供稳定、无网络依赖的 E2E 对话响应。"""

    def __init__(self, include_thinking: bool = False) -> None:
        self._include_thinking = include_thinking

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
    ) -> str:
        """返回固定回答，并按需添加 thinking 内容。"""
        if self._include_thinking:
            return MOCK_THINKING_PREFIX + MOCK_LLM_ANSWER
        return MOCK_LLM_ANSWER

    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
    ) -> AsyncGenerator[str, None]:
        """按固定分块流式返回回答，并按需先返回 thinking 内容。"""
        if self._include_thinking:
            yield MOCK_THINKING_PREFIX
        for index in range(0, len(MOCK_LLM_ANSWER), MOCK_CHUNK_SIZE):
            yield MOCK_LLM_ANSWER[index : index + MOCK_CHUNK_SIZE]
