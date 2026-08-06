"""Embedding provider 工厂与单例管理。

设计参考 :mod:`app.services.llm`：通过模块级缓存保存单例，
:func:`reset_embedding_provider` 用于测试间清理。
"""

import threading
from typing import Optional

from app.core.config import get_settings
from app.services.embedding.local import LocalSentenceTransformerProvider

_provider_instance: Optional[LocalSentenceTransformerProvider] = None
_provider_lock = threading.Lock()


def get_embedding_provider() -> LocalSentenceTransformerProvider:
    """返回当前配置的 embedding provider 单例。

    当前唯一实现是 :class:`LocalSentenceTransformerProvider`；后续如需远端
    实现（例如 OpenAI text-embedding-3），可在此处按 ``settings.embedding_provider``
    派发，与 :func:`app.services.llm.get_llm_provider` 的派发方式保持一致。
    """
    global _provider_instance
    if _provider_instance is None:
        with _provider_lock:
            if _provider_instance is None:
                # 在锁内读 settings 以保证单例参数与最近一次 ``reset_embedding_provider``
                # 之后的状态一致。
                settings = get_settings()
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
