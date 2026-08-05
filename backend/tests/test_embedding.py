"""Unit tests for Embedding Providers with mocked API calls."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.embedding import (
    OpenAIEmbeddingProvider,
    CohereEmbeddingProvider,
    get_embedding_provider,
    reset_providers,
)


@pytest.fixture(autouse=True)
def reset_provider_instances():
    """Reset provider singletons before and after each test."""
    reset_providers()
    yield
    reset_providers()


class TestOpenAIEmbeddingProvider:
    """Tests for OpenAI Embedding Provider."""

    @pytest.fixture
    def mock_embeddings(self):
        """Create mock embedding vectors."""
        return [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]

    @pytest.mark.asyncio
    async def test_embed_texts_returns_embeddings(self, mock_embeddings):
        """Test that embed_texts returns embeddings for multiple texts."""
        with patch("app.services.embedding.OpenAIEmbedding") as mock_embed_class:
            mock_embed_model = MagicMock()
            mock_embed_model.get_text_embedding_batch = MagicMock(return_value=mock_embeddings)
            mock_embed_class.return_value = mock_embed_model

            provider = OpenAIEmbeddingProvider()
            texts = ["Hello world", "Test text"]
            result = await provider.embed_texts(texts)

            assert result == mock_embeddings
            mock_embed_model.get_text_embedding_batch.assert_called_once_with(texts)

    @pytest.mark.asyncio
    async def test_embed_texts_empty_list(self):
        """Test that embed_texts returns empty list for empty input."""
        with patch("app.services.embedding.OpenAIEmbedding"):
            provider = OpenAIEmbeddingProvider()
            result = await provider.embed_texts([])

            assert result == []

    @pytest.mark.asyncio
    async def test_embed_text_returns_single_embedding(self):
        """Test that embed_text returns embedding for single text."""
        mock_embedding = [0.1, 0.2, 0.3]

        with patch("app.services.embedding.OpenAIEmbedding") as mock_embed_class:
            mock_embed_model = MagicMock()
            mock_embed_model.aget_text_embedding = AsyncMock(return_value=mock_embedding)
            mock_embed_class.return_value = mock_embed_model

            provider = OpenAIEmbeddingProvider()
            result = await provider.embed_text("Hello world")

            assert result == mock_embedding
            mock_embed_model.aget_text_embedding.assert_called_once_with("Hello world")

    def test_singleton_pattern(self):
        """Test that OpenAIEmbeddingProvider follows singleton pattern."""
        with patch("app.services.embedding.OpenAIEmbedding"):
            provider1 = OpenAIEmbeddingProvider()
            provider2 = OpenAIEmbeddingProvider()
            assert provider1 is provider2


class TestCohereEmbeddingProvider:
    """Tests for Cohere Embedding Provider."""

    @pytest.fixture
    def mock_cohere_response(self):
        """Create a mock Cohere API response."""
        mock_response = MagicMock()
        mock_response.embeddings = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        return mock_response

    @pytest.mark.asyncio
    async def test_embed_texts_returns_embeddings(self, mock_cohere_response):
        """Test that embed_texts returns embeddings for multiple texts."""
        with patch("app.services.embedding.CohereClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.embed = AsyncMock(return_value=mock_cohere_response)
            mock_client_class.return_value = mock_client

            provider = CohereEmbeddingProvider()
            texts = ["Hello world", "Test text"]
            result = await provider.embed_texts(texts)

            assert result == mock_cohere_response.embeddings
            mock_client.embed.assert_called_once()

            call_kwargs = mock_client.embed.call_args.kwargs
            assert call_kwargs["texts"] == texts
            assert call_kwargs["input_type"] == "clustering"

    @pytest.mark.asyncio
    async def test_embed_texts_empty_list(self):
        """Test that embed_texts returns empty list for empty input."""
        with patch("app.services.embedding.CohereClient"):
            provider = CohereEmbeddingProvider()
            result = await provider.embed_texts([])

            assert result == []

    @pytest.mark.asyncio
    async def test_embed_text_returns_single_embedding(self, mock_cohere_response):
        """Test that embed_text returns embedding for single text."""
        mock_response = MagicMock()
        mock_response.embeddings = [[0.1, 0.2, 0.3]]

        with patch("app.services.embedding.CohereClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.embed = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            provider = CohereEmbeddingProvider()
            result = await provider.embed_text("Hello world")

            assert result == [0.1, 0.2, 0.3]
            mock_client.embed.assert_called_once()

            call_kwargs = mock_client.embed.call_args.kwargs
            assert call_kwargs["texts"] == ["Hello world"]
            assert call_kwargs["input_type"] == "search_query"

    @pytest.mark.asyncio
    async def test_embed_texts_with_model(self, mock_cohere_response):
        """Test that embed_texts uses configured model."""
        with patch("app.services.embedding.CohereClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.embed = AsyncMock(return_value=mock_cohere_response)
            mock_client_class.return_value = mock_client

            provider = CohereEmbeddingProvider()
            await provider.embed_texts(["Hello"])

            call_kwargs = mock_client.embed.call_args.kwargs
            assert "model" in call_kwargs

    def test_singleton_pattern(self):
        """Test that CohereEmbeddingProvider follows singleton pattern."""
        with patch("app.services.embedding.CohereClient"):
            provider1 = CohereEmbeddingProvider()
            provider2 = CohereEmbeddingProvider()
            assert provider1 is provider2


class TestEmbeddingProviderFactory:
    """Tests for embedding provider factory function."""

    def test_get_embedding_provider_returns_openai_by_default(self):
        """Test that get_embedding_provider returns OpenAI by default."""
        with patch("app.services.embedding.settings") as mock_settings:
            mock_settings.embedding_provider = "openai"
            with patch("app.services.embedding.OpenAIEmbedding"):
                provider = get_embedding_provider()
                assert isinstance(provider, OpenAIEmbeddingProvider)

    def test_get_embedding_provider_returns_openai_for_openai_setting(self):
        """Test that get_embedding_provider returns OpenAI for 'openai' setting."""
        with patch("app.services.embedding.settings") as mock_settings:
            mock_settings.embedding_provider = "openai"
            with patch("app.services.embedding.OpenAIEmbedding"):
                provider = get_embedding_provider()
                assert isinstance(provider, OpenAIEmbeddingProvider)

    def test_get_embedding_provider_returns_cohere_for_cohere_setting(self):
        """Test that get_embedding_provider returns Cohere for 'cohere' setting."""
        with patch("app.services.embedding.settings") as mock_settings:
            mock_settings.embedding_provider = "cohere"
            with patch("app.services.embedding.CohereClient"):
                provider = get_embedding_provider()
                assert isinstance(provider, CohereEmbeddingProvider)

    def test_get_embedding_provider_case_insensitive(self):
        """Test that get_embedding_provider is case insensitive."""
        with patch("app.services.embedding.settings") as mock_settings:
            mock_settings.embedding_provider = "COHERE"
            with patch("app.services.embedding.CohereClient"):
                provider = get_embedding_provider()
                assert isinstance(provider, CohereEmbeddingProvider)


class TestResetEmbeddingProviders:
    """Tests for reset_providers function in embedding module."""

    def test_reset_providers_clears_singletons(self):
        """Test that reset_providers clears singleton instances."""
        with patch("app.services.embedding.OpenAIEmbedding"):
            with patch("app.services.embedding.CohereClient"):
                # Create providers
                provider1 = OpenAIEmbeddingProvider()
                provider2 = CohereEmbeddingProvider()

                # Reset
                reset_providers()

                # Create new instances
                provider3 = OpenAIEmbeddingProvider()
                provider4 = CohereEmbeddingProvider()

                # New instances should be different objects
                assert provider1 is not provider3
                assert provider2 is not provider4

    def test_reset_providers_allows_reinitialization(self):
        """Test that reset_providers allows providers to reinitialize."""
        with patch("app.services.embedding.CohereClient") as mock_cohere:
            # First initialization
            CohereEmbeddingProvider()
            reset_providers()

            # Second initialization with different settings
            CohereEmbeddingProvider()
            # Should have been called twice
            assert mock_cohere.call_count == 2
