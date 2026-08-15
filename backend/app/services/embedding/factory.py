"""Embedding provider 工厂与单例管理。

设计参考 :mod:`app.services.llm`：通过模块级缓存保存单例，
:func:`reset_embedding_provider` 用于测试间清理。
"""

import threading
from typing import Optional

from app.core.config import get_settings
from app.services.embedding.base import EmbeddingProvider
from app.services.embedding.local import LocalSentenceTransformerProvider

_provider_instance: Optional[EmbeddingProvider] = None
_provider_lock = threading.Lock()


def get_embedding_provider() -> EmbeddingProvider:
    """返回当前配置的 embedding provider 单例。

    通过 ``settings.embedding_provider`` 派发：

    - ``"mock"`` 返回 :class:`app.services.embedding.mock.MockEmbeddingProvider`，
      用于 E2E 测试避免加载 bge-m3。
    - ``"local"``（默认）返回 :class:`LocalSentenceTransformerProvider`。
    - ``"http"`` 返回 :class:`app.services.embedding.remote.RemoteEmbeddingProvider`，
      转发到 docker-compose 里的 Infinity 服务（OpenAI 兼容 /embeddings）。

    如需其他远端实现，在此处追加分支即可。
    """
    global _provider_instance
    if _provider_instance is None:
        with _provider_lock:
            if _provider_instance is None:
                # 在锁内读 settings 以保证单例参数与最近一次 ``reset_embedding_provider``
                # 之后的状态一致。
                settings = get_settings()
                if settings.embedding_provider == "mock":
                    from app.services.embedding.mock import MockEmbeddingProvider

                    _provider_instance = MockEmbeddingProvider(
                        model_name=settings.embedding_model,
                        dim=settings.embedding_dim,
                    )
                elif settings.embedding_provider == "http":
                    from app.services.embedding.remote import RemoteEmbeddingProvider

                    _provider_instance = RemoteEmbeddingProvider(
                        model_name=settings.embedding_model,
                        dim=settings.embedding_dim,
                    )
                else:
                    _provider_instance = LocalSentenceTransformerProvider(
                        model_name=settings.embedding_model,
                        dim=settings.embedding_dim,
                    )
    return _provider_instance


def reset_embedding_provider() -> None:
    """清空 embedding provider 单例；下一次 :func:`get_embedding_provider` 会重建。

    与 :func:`app.services.llm.reset_providers` 对齐，用于：

    - 单测间清理（避免 ``settings.embedding_dim`` 变更后旧实例残留）
    - 配置热更新时强制重新读取 settings
    """
    global _provider_instance
    _provider_instance = None
