from typing import List
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.core.settings import Settings
from app.core.config import get_settings

settings = get_settings()


class EmbeddingService:
    """Service for generating text embeddings using OpenAI."""

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
        
        embeddings = self._embed_model.get_text_embedding_batch(texts)
        return embeddings

    async def embed_text(self, text: str) -> List[float]:
        """Generate embedding for a single text."""
        return await self._embed_model.aget_text_embedding(text)
