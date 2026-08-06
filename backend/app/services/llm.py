from typing import Protocol, List, Dict, Any, AsyncGenerator, Optional

from openai import AsyncOpenAI
from anthropic import AsyncAnthropic
from starlette.requests import Request

from app.core.config import get_settings

settings = get_settings()


E2E_TEST_HEADER = "x-e2e-test"
E2E_MOCK_THINKING_HEADER = "x-e2e-mock-thinking"
MOCK_LLM_ANSWER = "Hello! I am a mocked DocQA assistant. (no real LLM was called)"
MOCK_CHUNK_SIZE = 5
MOCK_THINKING_PREFIX = "<think>Mock reasoning about the user's question.</think>"


class LLMProvider(Protocol):
    """Abstract interface for LLM providers."""

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
    ) -> str:
        """Send chat completion request and return the response text."""
        ...

    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
    ) -> AsyncGenerator[str, None]:
        """Send chat completion request with streaming and yield response chunks."""
        ...


class OpenAIProvider:
    """OpenAI LLM Provider implementation."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key or "sk-dummy-initial-key",
            base_url=settings.openai_base_url,
        )
        self._model = settings.openai_model or settings.llm_model

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
    ) -> str:
        """Send chat completion request and return the response text."""
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=temperature,
        )
        return response.choices[0].message.content or ""

    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
    ) -> AsyncGenerator[str, None]:
        """Send chat completion request with streaming and yield response chunks."""
        stream = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=temperature,
            stream=True,
        )
        async for chunk in stream:
            content = chunk.choices[0].delta.content
            if content:
                yield content


class AnthropicProvider:
    """Anthropic Claude LLM Provider implementation."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self._client = AsyncAnthropic(
            api_key=settings.anthropic_api_key or "sk-ant-dummy-initial-key",
            base_url=settings.anthropic_base_url,
        )
        self._model = settings.anthropic_model

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
    ) -> str:
        """Send chat completion request and return the response text."""
        # Convert messages format for Anthropic
        system_message = ""
        anthropic_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_message = msg["content"]
            else:
                anthropic_messages.append({
                    "role": msg["role"],
                    "content": msg["content"],
                })

        response = await self._client.messages.create(
            model=self._model,
            system=system_message or None,
            messages=anthropic_messages,
            temperature=temperature,
            max_tokens=4096,
        )
        return response.content[0].text

    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
    ) -> AsyncGenerator[str, None]:
        """Send chat completion request with streaming and yield response chunks."""
        # Convert messages format for Anthropic
        system_message = ""
        anthropic_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_message = msg["content"]
            else:
                anthropic_messages.append({
                    "role": msg["role"],
                    "content": msg["content"],
                })

        async with self._client.messages.stream(
            model=self._model,
            system=system_message or None,
            messages=anthropic_messages,
            temperature=temperature,
            max_tokens=4096,
        ) as stream:
            async for text_event in stream.text_stream:
                yield text_event


class MockLLMProvider:
    """Mock LLM provider used for E2E tests.

    Activated solely by the ``X-E2E-Test: true`` request header. Returns a
    fixed string in both ``chat`` and ``stream_chat`` so E2E suites can assert
    on a stable payload without touching the real LLM. RAG retrieval still
    runs against the real backend.

    Pass ``include_thinking=True`` to prepend a synthetic ``<think>...</think>``
    segment so tests can exercise the thinking-collapse UI without changing the
    default mock payload for unrelated cases.
    """

    def __init__(self, include_thinking: bool = False) -> None:
        self._include_thinking = include_thinking

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
    ) -> str:
        """Return the mock answer verbatim, optionally prefixed with thinking."""
        if self._include_thinking:
            return MOCK_THINKING_PREFIX + MOCK_LLM_ANSWER
        return MOCK_LLM_ANSWER

    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
    ) -> AsyncGenerator[str, None]:
        """Yield the mock answer in fixed-size chunks to mimic streaming.

        When ``include_thinking`` was set, the first yielded chunk carries the
        synthetic ``<think>...</think>`` segment so downstream splitting logic
        emits a ``thinking`` event before the answer chunks.
        """
        if self._include_thinking:
            yield MOCK_THINKING_PREFIX
        answer = MOCK_LLM_ANSWER
        for index in range(0, len(answer), MOCK_CHUNK_SIZE):
            yield answer[index : index + MOCK_CHUNK_SIZE]


def get_llm_provider(request: Optional[Request] = None) -> LLMProvider:
    """Factory function to get the configured LLM provider.

    When ``request`` carries the ``X-E2E-Test: true`` header, return
    :class:`MockLLMProvider` so E2E suites skip the real LLM call. The
    selection is request-scoped (no caching for the mock branch) so a single
    process can serve both mock and real traffic safely. The optional
    ``X-E2E-Mock-Thinking: true`` header further requests that the mock
    prepend a ``<think>...</think>`` segment for tests that exercise the
    thinking-collapse UI.
    """
    if request is not None and request.headers.get(E2E_TEST_HEADER, "").lower() == "true":
        include_thinking = (
            request.headers.get(E2E_MOCK_THINKING_HEADER, "").lower() == "true"
        )
        return MockLLMProvider(include_thinking=include_thinking)
    provider_type = settings.llm_provider.lower()
    if provider_type == "anthropic":
        return AnthropicProvider()
    return OpenAIProvider()


def reset_providers():
    """Clear LLM provider singletons so the next access rebuilds them with current settings."""
    OpenAIProvider._instance = None
    AnthropicProvider._instance = None


# Backward compatibility alias
LLMService = OpenAIProvider
