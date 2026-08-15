"""远端 HTTP embedding provider——调用 Infinity 服务的 OpenAI 兼容接口。

embedding 模型迁出后端进程后，后端不再承担 bge-m3 的加载与推理，而是
转发给 docker-compose 里常驻的 Infinity 服务（``POST /embeddings``）。
契约与 :class:`LocalSentenceTransformerProvider` 保持一致：``dim`` 构造期
确定、``embed_texts`` 同步阻塞（调用方负责 ``run_in_executor``）；请求失败
抛异常，由检索链路按既有约定降级为空结果。
"""
from typing import List, Optional

import httpx

from app.core.config import get_settings

# BAAI/bge-m3 输出维度（与 ``settings.embedding_dim`` 默认值保持一致）。
BGE_M3_DIM = 1024
DEFAULT_API_URL = "http://localhost:7997"


class RemoteEmbeddingProvider:
    """OpenAI 兼容 ``POST {api_url}/embeddings`` 的 embedding provider。

    行为契约：

    - ``dim`` 在 ``__init__`` 时即从 ``settings.embedding_dim`` 读取，运行期不变。
    - ``embed_texts`` 是同步阻塞（HTTP 往返）；调用方沿用 ``run_in_executor``
      策略，httpx 同步客户端在 executor 线程中执行不阻塞事件循环。
    - 远端返回的向量维度与 ``dim`` 不符时抛 ``RuntimeError``（配置错误应早
      暴露，而不是等到 pgvector 写入时报错）。
    - HTTP 错误 / 连接失败抛 httpx 异常，由 dense/rag 的检索降级路径吞掉。
    """

    def __init__(
        self,
        model_name: Optional[str] = None,
        dim: Optional[int] = None,
        api_url: Optional[str] = None,
        timeout: float = 60.0,
    ) -> None:
        # 允许注入（单测或后续动态切换）；默认从 settings 读。
        settings = get_settings()
        self._model_name = model_name or settings.embedding_model or "BAAI/bge-m3"
        self._dim = dim if dim is not None else (settings.embedding_dim or BGE_M3_DIM)
        self._api_url = (api_url or settings.embedding_api_url or DEFAULT_API_URL).rstrip("/")
        self._timeout = timeout

    @property
    def model_name(self) -> str:
        """已配置的 embedding 模型名（请求体 ``model`` 字段）。"""
        return self._model_name

    @property
    def dim(self) -> int:
        """Embedding 向量维度（与 ``settings.embedding_dim`` 对齐）。"""
        return self._dim

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """把 ``texts`` 编码为 ``List[List[float]]``。

        空列表直接返回 ``[]``，不发远端请求（与 local provider 行为一致）。
        """
        if not texts:
            return []
        response = httpx.post(
            f"{self._api_url}/embeddings",
            json={"model": self._model_name, "input": texts},
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or []
        vectors: List[List[float]] = []
        for item in data:
            vector = item.get("embedding") or []
            if len(vector) != self._dim:
                raise RuntimeError(
                    f"embedding 服务返回维度 {len(vector)} != 期望 {self._dim}（检查模型配置）"
                )
            vectors.append(list(vector))
        if len(vectors) != len(texts):
            raise RuntimeError(
                f"embedding 服务返回 {len(vectors)} 条向量 != 请求 {len(texts)} 条"
            )
        return vectors
