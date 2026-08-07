"""Mock embedding provider，返回确定性的零向量占位。

用途：
- E2E 测试环境，避免加载本地 bge-m3 模型（sentence-transformers + joblib 在
  Python 3.14 上首次加载有 loky semaphore 泄漏 / segfault 风险，参见
  ``docs/agents/issue-tracker.md`` 中 bge-m3 + loky 段）。
- 单元测试中需要 ``EmbeddingProvider`` 但不关心真实向量化语义的场景。

行为契约：
- ``dim`` 由 settings 读，与真实 provider 行为一致。
- ``embed_texts`` 对每个输入返回一个全 0 向量（``[0.0] * dim``）。
  - **不保证检索质量**；纯占位。E2E 测试应避免依赖真实 RAG 命中。
- ``is_mock = True`` 供调用方短路 Milvus 查询（零向量在 Milvus COSINE
  metric 下可能返回 distance=0 被误命中；mock provider 不应参与检索）。
"""

from typing import List, Optional

from app.core.config import get_settings


class MockEmbeddingProvider:
    """Mock embedding provider：返回零向量，避免加载本地模型。

    通过类属性 :attr:`is_mock` 让调用方（:mod:`app.services.rag`）短路
    Milvus 检索，避免零向量在 COSINE metric 下产生误命中。
    """

    is_mock = True

    def __init__(
        self,
        model_name: Optional[str] = None,
        dim: Optional[int] = None,
    ) -> None:
        settings = get_settings()
        self._model_name = model_name or settings.embedding_model or "mock-embedding"
        self._dim = dim if dim is not None else (settings.embedding_dim or 1024)

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dim(self) -> int:
        return self._dim

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        zero = [0.0] * self._dim
        return [list(zero) for _ in texts]
