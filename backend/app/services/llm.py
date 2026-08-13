from typing import Protocol, List, Dict, AsyncGenerator, Optional

from openai import AsyncOpenAI
from anthropic import AsyncAnthropic
from starlette.requests import Request

from app.core.config import get_settings
from app.services import mock_llm

settings = get_settings()


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


def get_llm_provider(request: Optional[Request] = None) -> LLMProvider:
    """Factory function to get the configured LLM provider.

    When ``request`` carries the ``X-E2E-Test: true`` header, return
    :class:`MockLLMProvider` so E2E suites skip the real LLM call. The
    selection is request-scoped (no caching for the mock branch) so a single
    process can serve both mock and real traffic safely. The optional
    ``X-E2E-Mock-Thinking: true`` header further requests that the mock
    prepend a ``<think>...</think>`` segment for tests that exercise the
    thinking-collapse UI. ``#45`` ``X-E2E-Mock-LLM-Error: true`` 让 mock 在
    ``chat`` / ``stream_chat`` 抛 ``RuntimeError``，用于稳定测试运行时
    LLM 不可用路径。
    """
    if (
        request is not None
        and request.headers.get(mock_llm.E2E_TEST_HEADER, "").lower() == "true"
    ):
        include_thinking = (
            request.headers.get(mock_llm.E2E_MOCK_THINKING_HEADER, "").lower()
            == "true"
        )
        fail_with_error = (
            request.headers.get(mock_llm.E2E_MOCK_LLM_ERROR_HEADER, "").lower()
            == "true"
        )
        return mock_llm.MockLLMProvider(
            include_thinking=include_thinking,
            fail_with_error=fail_with_error,
        )
    provider_type = settings.llm_provider.lower()
    if provider_type == "anthropic":
        return AnthropicProvider()
    return OpenAIProvider()


def reset_providers():
    """Clear LLM provider singletons so the next access rebuilds them with current settings."""
    OpenAIProvider._instance = None
    AnthropicProvider._instance = None


# #45：preflight 阶段检测当前 LLM Provider 是否具备 API Key + Model；
# 返回 ``(configured, reason)``。``configured=True`` 时 ``reason`` 为空串。
def is_llm_configured() -> tuple[bool, str]:
    current = get_settings()
    provider_type = current.llm_provider.lower()
    if provider_type == "anthropic":
        api_key = current.anthropic_api_key
        model = current.anthropic_model
        provider_label = "Anthropic"
    else:  # openai（默认）
        api_key = current.openai_api_key
        model = current.openai_model
        provider_label = "OpenAI"

    if not api_key or not api_key.strip():
        return False, f"{provider_label} API Key 未配置"
    if not model or not model.strip():
        return False, f"{provider_label} Model 未配置"
    return True, ""


# Backward compatibility alias
LLMService = OpenAIProvider
