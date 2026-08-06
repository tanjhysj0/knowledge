"""Embedding provider 抽象接口。

``EmbeddingProvider`` 是 ``typing.Protocol``：它只规定**调用方**所需的最小行为，
不要求实现类显式继承。这样既保留了静态鸭子类型，也允许单测用 ``unittest.mock``
直接伪造实现。
"""

from typing import List, Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Embedding provider 抽象协议。

    调用方（目前是 :mod:`app.services.rag` 与 :mod:`app.services.vector_store`）
    仅依赖两个成员：

    - ``dim``：向量化维度的 ``int`` 属性（构造时即确定，运行时不变）
    - ``embed_texts(texts)``：把字符串列表映射为 ``List[List[float]]``，每段
      向量长度必须等于 ``dim``

    实现类可以是本地模型（``LocalSentenceTransformerProvider``）或远端 API；
    任何满足该协议的实例都可以通过 :func:`get_embedding_provider` 工厂注入。
    """

    @property
    def dim(self) -> int:
        """Embedding 向量维度（在 ``__init__`` 时确定，运行期不可变）。"""
        ...

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """把 ``texts`` 编码为 ``List[List[float]]``。

        返回值长度必须等于 ``len(texts)``，每个子列表长度必须等于 ``self.dim``。
        实现可以是同步阻塞（sentence-transformers.encode 是 CPU 密集型）或
        异步；协议不强制 async 以兼容 numpy / 同步第三方库。
        """
        ...
