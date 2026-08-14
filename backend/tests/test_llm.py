"""Unit tests for LLM Providers with mocked API calls."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.llm import (
    OpenAIProvider,
    AnthropicProvider,
    get_llm_provider,
    reset_providers,
    is_llm_configured,
)


@pytest.fixture(autouse=True)
def reset_provider_instances():
    """Reset provider singletons before and after each test."""
    reset_providers()
    yield
    reset_providers()


class TestOpenAIProvider:
    """Tests for OpenAI LLM Provider."""

    @pytest.fixture
    def mock_openai_response(self):
        """Create a mock OpenAI API response."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello, this is OpenAI response!"
        return mock_response

    @pytest.mark.asyncio
    async def test_chat_returns_response_text(self, mock_openai_response):
        """Test that chat returns the assistant's response text."""
        with patch("app.services.llm.AsyncOpenAI") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_openai_response)
            mock_client_class.return_value = mock_client

            provider = OpenAIProvider()
            messages = [{"role": "user", "content": "Hello"}]
            result = await provider.chat(messages)

            assert result == "Hello, this is OpenAI response!"
            mock_client.chat.completions.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_chat_with_temperature(self, mock_openai_response):
        """Test that chat respects temperature parameter."""
        with patch("app.services.llm.AsyncOpenAI") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_openai_response)
            mock_client_class.return_value = mock_client

            provider = OpenAIProvider()
            messages = [{"role": "user", "content": "Hello"}]
            await provider.chat(messages, temperature=0.5)

            call_kwargs = mock_client.chat.completions.create.call_args.kwargs
            assert call_kwargs["temperature"] == 0.5

    @pytest.mark.asyncio
    async def test_chat_empty_content(self):
        """Test that chat handles empty content gracefully."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None

        with patch("app.services.llm.AsyncOpenAI") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            provider = OpenAIProvider()
            messages = [{"role": "user", "content": "Hello"}]
            result = await provider.chat(messages)

            assert result == ""

    @pytest.mark.asyncio
    async def test_stream_chat_yields_chunks(self):
        """Test that stream_chat yields response chunks."""
        async def mock_stream():
            chunks = [
                MagicMock(choices=[MagicMock(delta=MagicMock(content="Hello"))]),
                MagicMock(choices=[MagicMock(delta=MagicMock(content=" world"))]),
                MagicMock(choices=[MagicMock(delta=MagicMock(content="!"))]),
            ]
            for chunk in chunks:
                yield chunk

        with patch("app.services.llm.AsyncOpenAI") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_stream())
            mock_client_class.return_value = mock_client

            provider = OpenAIProvider()
            messages = [{"role": "user", "content": "Hello"}]
            chunks = []
            async for chunk in provider.stream_chat(messages):
                chunks.append(chunk)

            assert chunks == ["Hello", " world", "!"]

    @pytest.mark.asyncio
    async def test_stream_chat_skips_empty_content(self):
        """Test that stream_chat skips chunks with empty content."""
        async def mock_stream():
            chunks = [
                MagicMock(choices=[MagicMock(delta=MagicMock(content="Hello"))]),
                MagicMock(choices=[MagicMock(delta=MagicMock(content=None))]),  # Empty
                MagicMock(choices=[MagicMock(delta=MagicMock(content="!"))]),
            ]
            for chunk in chunks:
                yield chunk

        with patch("app.services.llm.AsyncOpenAI") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_stream())
            mock_client_class.return_value = mock_client

            provider = OpenAIProvider()
            messages = [{"role": "user", "content": "Hello"}]
            chunks = []
            async for chunk in provider.stream_chat(messages):
                chunks.append(chunk)

            assert chunks == ["Hello", "!"]

    def test_singleton_pattern(self):
        """Test that OpenAIProvider follows singleton pattern."""
        with patch("app.services.llm.AsyncOpenAI"):
            provider1 = OpenAIProvider()
            provider2 = OpenAIProvider()
            assert provider1 is provider2


class TestAnthropicProvider:
    """Tests for Anthropic LLM Provider."""

    @pytest.fixture
    def mock_anthropic_response(self):
        """Create a mock Anthropic API response."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = "Hello, this is Anthropic response!"
        return mock_response

    @pytest.mark.asyncio
    async def test_chat_returns_response_text(self, mock_anthropic_response):
        """Test that chat returns the assistant's response text."""
        with patch("app.services.llm.AsyncAnthropic") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_anthropic_response)
            mock_client_class.return_value = mock_client

            provider = AnthropicProvider()
            messages = [{"role": "user", "content": "Hello"}]
            result = await provider.chat(messages)

            assert result == "Hello, this is Anthropic response!"
            mock_client.messages.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_chat_with_system_message(self, mock_anthropic_response):
        """Test that chat handles system messages correctly."""
        with patch("app.services.llm.AsyncAnthropic") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_anthropic_response)
            mock_client_class.return_value = mock_client

            provider = AnthropicProvider()
            messages = [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hello"},
            ]
            await provider.chat(messages)

            call_kwargs = mock_client.messages.create.call_args.kwargs
            assert call_kwargs["system"] == "You are a helpful assistant."
            assert call_kwargs["messages"] == [{"role": "user", "content": "Hello"}]

    @pytest.mark.asyncio
    async def test_chat_with_temperature(self, mock_anthropic_response):
        """Test that chat respects temperature parameter."""
        with patch("app.services.llm.AsyncAnthropic") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.messages.create = AsyncMock(return_value=mock_anthropic_response)
            mock_client_class.return_value = mock_client

            provider = AnthropicProvider()
            messages = [{"role": "user", "content": "Hello"}]
            await provider.chat(messages, temperature=0.3)

            call_kwargs = mock_client.messages.create.call_args.kwargs
            assert call_kwargs["temperature"] == 0.3

    @pytest.mark.asyncio
    async def test_stream_chat_yields_chunks(self):
        """Test that stream_chat yields response chunks."""
        chunks_data = ["Hello", " world", "!"]

        # Create a mock async iterator for text_stream
        class MockAsyncIterator:
            def __init__(self, data):
                self.data = data
                self.index = 0

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self.index >= len(self.data):
                    raise StopAsyncIteration
                result = self.data[self.index]
                self.index += 1
                return result

        mock_stream = MagicMock()
        mock_stream.text_stream = MockAsyncIterator(chunks_data)
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)

        mock_client = MagicMock()
        mock_client.messages.stream = MagicMock(return_value=mock_stream)

        with patch("app.services.llm.AsyncAnthropic") as mock_client_class:
            mock_client_class.return_value = mock_client

            provider = AnthropicProvider()
            messages = [{"role": "user", "content": "Hello"}]
            chunks = []
            async for chunk in provider.stream_chat(messages):
                chunks.append(chunk)

            assert chunks == ["Hello", " world", "!"]

    @pytest.mark.asyncio
    async def test_stream_chat_with_system_message(self):
        """Test that stream_chat handles system messages correctly."""
        mock_stream = AsyncMock()
        mock_stream.text_stream = AsyncMock(return_value=iter(["Response"]))

        mock_client = MagicMock()
        mock_client.messages.stream = MagicMock(return_value=mock_stream)

        with patch("app.services.llm.AsyncAnthropic") as mock_client_class:
            mock_client_class.return_value = mock_client

            provider = AnthropicProvider()
            messages = [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hello"},
            ]
            async for _ in provider.stream_chat(messages):
                pass

            call_kwargs = mock_client.messages.stream.call_args.kwargs
            assert call_kwargs["system"] == "You are a helpful assistant."
            assert call_kwargs["messages"] == [{"role": "user", "content": "Hello"}]

    def test_singleton_pattern(self):
        """Test that AnthropicProvider follows singleton pattern."""
        with patch("app.services.llm.AsyncAnthropic"):
            provider1 = AnthropicProvider()
            provider2 = AnthropicProvider()
            assert provider1 is provider2


class TestLLMProviderFactory:
    """Tests for LLM provider factory function.

    #69：provider 选择改读运行时默认模型单例，测试 patch
    ``app.services.llm.get_runtime_model`` 注入假运行时配置。
    """

    @staticmethod
    def _patch_runtime(monkeypatch, provider_type: str):
        from app.services.runtime_config import RuntimeModelConfig

        monkeypatch.setattr(
            "app.services.llm.get_runtime_model",
            lambda: RuntimeModelConfig(provider_type=provider_type),
        )

    def test_get_llm_provider_returns_openai_by_default(self, monkeypatch):
        """Test that get_llm_provider returns OpenAI by default."""
        self._patch_runtime(monkeypatch, "openai")
        with patch("app.services.llm.AsyncOpenAI"):
            provider = get_llm_provider()
            assert isinstance(provider, OpenAIProvider)

    def test_get_llm_provider_returns_openai_for_openai_setting(self, monkeypatch):
        """Test that get_llm_provider returns OpenAI for 'openai' setting."""
        self._patch_runtime(monkeypatch, "openai")
        with patch("app.services.llm.AsyncOpenAI"):
            provider = get_llm_provider()
            assert isinstance(provider, OpenAIProvider)

    def test_get_llm_provider_returns_anthropic_for_anthropic_setting(
        self, monkeypatch
    ):
        """Test that get_llm_provider returns Anthropic for 'anthropic' setting."""
        self._patch_runtime(monkeypatch, "anthropic")
        with patch("app.services.llm.AsyncAnthropic"):
            provider = get_llm_provider()
            assert isinstance(provider, AnthropicProvider)

    def test_get_llm_provider_case_insensitive(self, monkeypatch):
        """Test that get_llm_provider is case insensitive."""
        self._patch_runtime(monkeypatch, "ANTHROPIC")
        with patch("app.services.llm.AsyncAnthropic"):
            provider = get_llm_provider()
            assert isinstance(provider, AnthropicProvider)


class TestResetProviders:
    """Tests for reset_providers function."""

    def test_reset_providers_clears_singletons(self):
        """Test that reset_providers clears singleton instances."""
        with patch("app.services.llm.AsyncOpenAI"):
            with patch("app.services.llm.AsyncAnthropic"):
                # Create providers
                provider1 = OpenAIProvider()
                provider2 = AnthropicProvider()

                # Reset
                reset_providers()

                # Create new instances
                provider3 = OpenAIProvider()
                provider4 = AnthropicProvider()

                # New instances should be different objects
                assert provider1 is not provider3
                assert provider2 is not provider4

    def test_reset_providers_allows_reinitialization(self):
        """Test that reset_providers allows providers to reinitialize."""
        with patch("app.services.llm.AsyncOpenAI") as mock_openai:
            # First initialization
            OpenAIProvider()
            reset_providers()

            # Second initialization with different settings
            OpenAIProvider()
            # Should have been called twice
            assert mock_openai.call_count == 2


class TestIsLLMConfigured:
    """Tests for is_llm_configured() — #45 preflight availability check.

    #69：读取源切换为运行时默认模型单例，测试改为 patch
    ``app.services.llm.get_runtime_model`` 注入假运行时配置。
    """

    def _patch_runtime(self, monkeypatch, **fields):
        """Patch ``get_runtime_model`` inside ``app.services.llm``."""
        from app.services.runtime_config import RuntimeModelConfig

        instance = RuntimeModelConfig(**fields)
        monkeypatch.setattr(
            "app.services.llm.get_runtime_model", lambda: instance
        )
        return instance

    def test_openai_missing_api_key(self, monkeypatch):
        self._patch_runtime(
            monkeypatch, provider_type="openai", api_key="", model_name="gpt-4o-mini"
        )
        configured, reason = is_llm_configured()
        assert configured is False
        assert "API Key" in reason or "key" in reason.lower()

    def test_openai_whitespace_api_key(self, monkeypatch):
        self._patch_runtime(
            monkeypatch, provider_type="openai", api_key="   ", model_name="gpt-4o-mini"
        )
        configured, reason = is_llm_configured()
        assert configured is False
        assert reason

    def test_openai_missing_model(self, monkeypatch):
        self._patch_runtime(
            monkeypatch, provider_type="openai", api_key="sk-valid-key", model_name=""
        )
        configured, reason = is_llm_configured()
        assert configured is False
        assert "Model" in reason or "model" in reason.lower()

    def test_openai_fully_configured(self, monkeypatch):
        self._patch_runtime(
            monkeypatch, provider_type="openai", api_key="sk-valid-key", model_name="gpt-4o-mini"
        )
        configured, reason = is_llm_configured()
        assert configured is True
        assert reason == ""

    def test_anthropic_missing_api_key(self, monkeypatch):
        self._patch_runtime(
            monkeypatch, provider_type="anthropic", api_key="", model_name="claude-3-5-sonnet"
        )
        configured, reason = is_llm_configured()
        assert configured is False
        assert "API Key" in reason or "key" in reason.lower()

    def test_anthropic_missing_model(self, monkeypatch):
        self._patch_runtime(
            monkeypatch, provider_type="anthropic", api_key="sk-ant-valid", model_name=""
        )
        configured, reason = is_llm_configured()
        assert configured is False
        assert "Model" in reason or "model" in reason.lower()

    def test_anthropic_fully_configured(self, monkeypatch):
        self._patch_runtime(
            monkeypatch,
            provider_type="anthropic",
            api_key="sk-ant-valid",
            model_name="claude-3-5-sonnet",
        )
        configured, reason = is_llm_configured()
        assert configured is True
        assert reason == ""

    def test_unknown_provider_treated_as_openai(self, monkeypatch):
        """Unknown provider value falls through to the OpenAI branch."""
        self._patch_runtime(
            monkeypatch, provider_type="unknown-thing", api_key="", model_name=""
        )
        configured, _ = is_llm_configured()
        assert configured is False
