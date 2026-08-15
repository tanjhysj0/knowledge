"""Embedding provider 抽象与实现。

当前内置：
- :class:`LocalSentenceTransformerProvider`：本地 sentence-transformers 实现，
  默认模型 ``BAAI/bge-m3``（中英多语言、``dim=1024``）。
- :class:`RemoteEmbeddingProvider`：转发到 Infinity 独立服务（OpenAI 兼容
  ``/embeddings``），模型常驻容器内，后端重启无冷启动。

工厂函数 :func:`get_embedding_provider` 暴露在 :mod:`app.services.embedding` 顶 层。
"""

from app.services.embedding.base import EmbeddingProvider
from app.services.embedding.factory import (
    get_embedding_provider,
    reset_embedding_provider,
)
from app.services.embedding.local import LocalSentenceTransformerProvider
from app.services.embedding.remote import RemoteEmbeddingProvider

__all__ = [
    "EmbeddingProvider",
    "LocalSentenceTransformerProvider",
    "RemoteEmbeddingProvider",
    "get_embedding_provider",
    "reset_embedding_provider",
]
