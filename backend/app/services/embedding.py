from typing import Protocol, List
from llama_index.embeddings.openai import OpenAIEmbedding
from cohere import AsyncClient as CohereClient
from app.core.config import get_settings

settings = get_settings()


class EmbeddingProvider(Protocol):
    """Abstract interface for embedding providers."""

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for a list of texts."""
        ...

    async def embed_text(self, text: str) -> List[float]:
        """Generate embedding for a single text."""
        ...


class OpenAIEmbeddingProvider:
    """OpenAI Embedding Provider implementation."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._embed_model = OpenAIEmbedding(
            model=settings.embedding_model,
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
        )

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for a list of texts."""
        if not texts:
            return []
        return self._embed_model.get_text_embedding_batch(texts)

    async def embed_text(self, text: str) -> List[float]:
        """Generate embedding for a single text."""
        return await self._embed_model.aget_text_embedding(text)


class CohereEmbeddingProvider:
    """Cohere Embedding Provider implementation."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._client = CohereClient(
            api_key=settings.cohere_api_key,
            base_url=settings.cohere_base_url,
        )
        self._model = settings.cohere_embedding_model

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for a list of texts."""
        if not texts:
            return []
        response = await self._client.embed(
            texts=texts,
            model=self._model,
            input_type="clustering",
        )
        return response.embeddings

    async def embed_text(self, text: str) -> List[float]:
        """Generate embedding for a single text."""
        response = await self._client.embed(
            texts=[text],
            model=self._model,
            input_type="search_query",
        )
        return response.embeddings[0]


def get_embedding_provider() -> EmbeddingProvider:
    """Factory function to get the configured embedding provider."""
    provider_type = settings.embedding_provider.lower()
    if provider_type == "cohere":
        return CohereEmbeddingProvider()
    return OpenAIEmbeddingProvider()


def reset_providers():
    """Reset all embedding provider instances to allow reinitialization with new settings."""
    OpenAIEmbeddingProvider._instance = None
    CohereEmbeddingProvider._instance = None


# Backward compatibility alias
EmbeddingService = OpenAIEmbeddingProvider
