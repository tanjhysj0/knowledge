"""#45 E2E 用的 ``X-E2E-Mock-LLM-Error`` 头部支持测试。"""
import pytest

from app.services.llm import get_llm_provider
from app.services.mock_llm import (
    E2E_MOCK_LLM_ERROR_HEADER,
    MOCK_LLM_ERROR_MESSAGE,
    MockLLMProvider,
)


class TestMockLLMProviderError:
    """``MockLLMProvider(fail_with_error=True)`` 模拟运行时 LLM 不可用。"""

    @pytest.mark.asyncio
    async def test_chat_raises_when_fail_with_error(self):
        provider = MockLLMProvider(fail_with_error=True)
        with pytest.raises(RuntimeError, match=MOCK_LLM_ERROR_MESSAGE):
            await provider.chat(messages=[{"role": "user", "content": "hi"}])

    @pytest.mark.asyncio
    async def test_stream_chat_raises_when_fail_with_error(self):
        provider = MockLLMProvider(fail_with_error=True)
        with pytest.raises(RuntimeError, match=MOCK_LLM_ERROR_MESSAGE):
            async for _ in provider.stream_chat(messages=[{"role": "user", "content": "hi"}]):
                pass

    @pytest.mark.asyncio
    async def test_default_provider_still_returns_mock_answer(self):
        """向后兼容：``fail_with_error`` 默认 ``False`` 保持原行为。"""
        provider = MockLLMProvider()
        result = await provider.chat(messages=[{"role": "user", "content": "hi"}])
        assert "mocked DocQA assistant" in result


class TestGetLLMProviderE2EHeaders:
    """``get_llm_provider`` 识别 ``X-E2E-Mock-LLM-Error`` 头部。"""

    def _headers(self, **values):
        """``values`` keys 用 ``_`` 代替 ``-``，构造形如 ``x-e2e-<key>`` 的小写头。"""
        from types import SimpleNamespace

        return SimpleNamespace(
            headers={f"x-e2e-{k.replace('_', '-')}": v for k, v in values.items()}
        )

    def test_returns_error_injecting_mock_when_header_true(self):
        request = self._headers(test="true", mock_llm_error="true")
        provider = get_llm_provider(request=request)
        assert isinstance(provider, MockLLMProvider)
        assert provider._fail_with_error is True

    def test_returns_thinking_mock_when_thinking_header_only(self):
        request = self._headers(test="true", mock_thinking="true")
        provider = get_llm_provider(request=request)
        assert isinstance(provider, MockLLMProvider)
        assert provider._fail_with_error is False
        assert provider._include_thinking is True

    def test_returns_plain_mock_when_only_test_header(self):
        request = self._headers(test="true")
        provider = get_llm_provider(request=request)
        assert isinstance(provider, MockLLMProvider)
        assert provider._fail_with_error is False
        assert provider._include_thinking is False
