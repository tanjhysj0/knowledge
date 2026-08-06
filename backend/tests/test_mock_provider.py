"""Unit tests for the E2E LLM mock driven by the ``X-E2E-Test`` header."""
from unittest.mock import patch

import pytest

from app.services.llm import (
    MOCK_CHUNK_SIZE,
    MOCK_LLM_ANSWER,
    MockLLMProvider,
    OpenAIProvider,
    get_llm_provider,
)
from app.services.rag import RAGService


def _make_request(header_value):
    """Build a minimal Starlette ``Request`` with a single header.

    The factory only configures the header so the LLM factory can read it;
    no body, query params, or app state are required.
    """
    from starlette.requests import Request

    raw_headers = []
    if header_value is not None:
        raw_headers.append((b"x-e2e-test", header_value.lower().encode()))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/chat",
        "raw_path": b"/api/chat",
        "headers": raw_headers,
        "query_string": b"",
    }
    return Request(scope)


class TestMockLLMProvider:
    """``MockLLMProvider`` must return a deterministic, network-free payload."""

    @pytest.mark.asyncio
    async def test_chat_returns_constant_answer(self):
        provider = MockLLMProvider()
        result = await provider.chat([{"role": "user", "content": "Hi"}])
        assert result == MOCK_LLM_ANSWER

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


class TestGetLLMProviderHeaderSwitch:
    """``get_llm_provider`` must select ``MockLLMProvider`` only when the header is set."""

    def test_returns_real_provider_when_header_absent(self):
        request = _make_request(None)
        with patch("app.services.llm.settings", llm_provider="openai"):
            with patch("app.services.llm.AsyncOpenAI"):
                provider = get_llm_provider(request)
        assert isinstance(provider, OpenAIProvider)

    def test_returns_mock_when_header_true(self):
        request = _make_request("true")
        provider = get_llm_provider(request)
        assert isinstance(provider, MockLLMProvider)

    def test_header_value_is_case_insensitive(self):
        request = _make_request("TRUE")
        provider = get_llm_provider(request)
        assert isinstance(provider, MockLLMProvider)

    def test_header_value_other_than_true_falls_back_to_real_provider(self):
        request = _make_request("false")
        with patch("app.services.llm.AsyncOpenAI"):
            provider = get_llm_provider(request)
        assert isinstance(provider, OpenAIProvider)

    def test_request_none_still_returns_real_provider(self):
        with patch("app.services.llm.AsyncOpenAI"):
            provider = get_llm_provider(None)
        assert isinstance(provider, OpenAIProvider)


class TestRAGServiceHeaderPropagation:
    """``RAGService._llm`` must propagate the request so the factory can read the header."""

    def test_llm_returns_mock_when_request_carries_header(self):
        request = _make_request("true")
        rag = RAGService(request=request)
        assert isinstance(rag._llm(), MockLLMProvider)

    def test_llm_returns_real_provider_when_request_missing_header(self):
        with patch("app.services.llm.AsyncOpenAI"):
            rag = RAGService(request=None)
            assert isinstance(rag._llm(), OpenAIProvider)