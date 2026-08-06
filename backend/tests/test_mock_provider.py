"""Unit tests for the E2E LLM mock driven by the ``X-E2E-Test`` header."""
from unittest.mock import patch

import pytest

from app.services.llm import (
    E2E_MOCK_THINKING_HEADER,
    MOCK_CHUNK_SIZE,
    MOCK_LLM_ANSWER,
    MOCK_THINKING_PREFIX,
    MockLLMProvider,
    OpenAIProvider,
    get_llm_provider,
)
from app.services.rag import RAGService


def _make_request(headers):
    """Build a minimal Starlette ``Request`` from a mapping of header names to values.

    Header names are case-insensitive; pass lowercase keys for clarity.
    """
    from starlette.requests import Request

    raw_headers = [(name.lower().encode(), value.lower().encode()) for name, value in headers.items()]
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/chat",
        "raw_path": b"/api/chat",
        "headers": raw_headers,
        "query_string": b"",
    }
    return Request(scope)


def _request_with_mock(test_header_value="true", thinking_header_value=None):
    """Build a request that triggers ``MockLLMProvider``.

    The thinking header is optional; when omitted the mock defaults to
    ``include_thinking=False`` for backward compat with tests that do not
    exercise the thinking-collapse UI.
    """
    headers = {"x-e2e-test": test_header_value}
    if thinking_header_value is not None:
        headers[E2E_MOCK_THINKING_HEADER] = thinking_header_value
    return _make_request(headers)


class TestMockLLMProvider:
    """``MockLLMProvider`` must return a deterministic, network-free payload."""

    @pytest.mark.asyncio
    async def test_chat_returns_constant_answer(self):
        provider = MockLLMProvider()
        result = await provider.chat([{"role": "user", "content": "Hi"}])
        assert result == MOCK_LLM_ANSWER

    @pytest.mark.asyncio
    async def test_chat_with_thinking_prefixes_think_block(self):
        provider = MockLLMProvider(include_thinking=True)
        result = await provider.chat([{"role": "user", "content": "Hi"}])
        assert result.startswith(MOCK_THINKING_PREFIX)
        assert "<think>" in result
        assert "</think>" in result
        assert result.endswith(MOCK_LLM_ANSWER)

    @pytest.mark.asyncio
    async def test_stream_chat_yields_chunks_in_order(self):
        provider = MockLLMProvider()
        chunks = []
        async for chunk in provider.stream_chat([{"role": "user", "content": "Hi"}]):
            chunks.append(chunk)
        joined = "".join(chunks)
        assert joined == MOCK_LLM_ANSWER
        assert all(len(chunk) <= MOCK_CHUNK_SIZE for chunk in chunks)
        assert len(chunks) > 1, "stream_chat should yield more than one chunk"

    @pytest.mark.asyncio
    async def test_stream_chat_with_thinking_yields_think_block_first(self):
        provider = MockLLMProvider(include_thinking=True)
        chunks = []
        async for chunk in provider.stream_chat([{"role": "user", "content": "Hi"}]):
            chunks.append(chunk)
        # First chunk must carry the full think block (single yield) so the
        # downstream splitter can emit a single ``thinking`` event rather than
        # fragmenting it across the chunked answer stream.
        assert chunks[0] == MOCK_THINKING_PREFIX
        joined_after_thinking = "".join(chunks[1:])
        assert joined_after_thinking == MOCK_LLM_ANSWER


class TestGetLLMProviderHeaderSwitch:
    """``get_llm_provider`` must select ``MockLLMProvider`` only when the header is set."""

    def test_returns_real_provider_when_header_absent(self):
        request = _make_request({})
        with patch("app.services.llm.settings", llm_provider="openai"):
            with patch("app.services.llm.AsyncOpenAI"):
                provider = get_llm_provider(request)
        assert isinstance(provider, OpenAIProvider)

    def test_returns_mock_when_header_true(self):
        request = _request_with_mock(test_header_value="true")
        provider = get_llm_provider(request)
        assert isinstance(provider, MockLLMProvider)
        assert provider._include_thinking is False

    def test_header_value_is_case_insensitive(self):
        request = _request_with_mock(test_header_value="TRUE")
        provider = get_llm_provider(request)
        assert isinstance(provider, MockLLMProvider)

    def test_header_value_other_than_true_falls_back_to_real_provider(self):
        request = _request_with_mock(test_header_value="false")
        with patch("app.services.llm.AsyncOpenAI"):
            provider = get_llm_provider(request)
        assert isinstance(provider, OpenAIProvider)

    def test_request_none_still_returns_real_provider(self):
        with patch("app.services.llm.AsyncOpenAI"):
            provider = get_llm_provider(None)
        assert isinstance(provider, OpenAIProvider)

    def test_mock_thinking_header_true_enables_include_thinking(self):
        request = _request_with_mock(test_header_value="true", thinking_header_value="true")
        provider = get_llm_provider(request)
        assert isinstance(provider, MockLLMProvider)
        assert provider._include_thinking is True

    def test_mock_thinking_header_other_value_disables_include_thinking(self):
        request = _request_with_mock(test_header_value="true", thinking_header_value="false")
        provider = get_llm_provider(request)
        assert isinstance(provider, MockLLMProvider)
        assert provider._include_thinking is False

    def test_mock_thinking_header_case_insensitive(self):
        request = _request_with_mock(test_header_value="true", thinking_header_value="TRUE")
        provider = get_llm_provider(request)
        assert isinstance(provider, MockLLMProvider)
        assert provider._include_thinking is True


class TestRAGServiceHeaderPropagation:
    """``RAGService._llm`` must propagate the request so the factory can read the header."""

    def test_llm_returns_mock_when_request_carries_header(self):
        request = _request_with_mock(test_header_value="true")
        rag = RAGService(request=request)
        assert isinstance(rag._llm(), MockLLMProvider)

    def test_llm_returns_real_provider_when_request_missing_header(self):
        with patch("app.services.llm.AsyncOpenAI"):
            rag = RAGService(request=None)
            assert isinstance(rag._llm(), OpenAIProvider)

    def test_llm_propagates_thinking_header_to_mock(self):
        request = _request_with_mock(test_header_value="true", thinking_header_value="true")
        rag = RAGService(request=request)
        provider = rag._llm()
        assert isinstance(provider, MockLLMProvider)
        assert provider._include_thinking is True