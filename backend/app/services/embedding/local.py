"""本地 sentence-transformers embedding provider。

默认模型 ``BAAI/bge-m3``（中英多语言、``dim=1024``），模型在首次 ``embed_texts``
调用时 lazy 加载，避免模块导入时阻塞启动。
"""

import threading
from typing import List, Optional

from sentence_transformers import SentenceTransformer

from app.core.config import get_settings

# BAAI/bge-m3 输出维度（与 ``settings.embedding_dim`` 默认值保持一致）。
BGE_M3_DIM = 1024
DEFAULT_MODEL_NAME = "BAAI/bge-m3"


class LocalSentenceTransformerProvider:
    """本地 sentence-transformers embedding provider。

    行为契约：

    - ``dim`` 在 ``__init__`` 时即从 ``settings.embedding_dim`` 读取，运行期不变。
    - 模型对象在首次 :meth:`embed_texts` 调用时 lazy 加载；不调用则不下载/不占内存。
    - ``embed_texts`` 是同步阻塞（CPU 密集）；调用方负责 ``run_in_executor``。
    - 向量结果以 Python ``list[float]`` 返回（numpy 数组 ``.tolist()``），避免
      JSON 序列化或 Milvus 写入时类型不匹配。
    """

    def __init__(
        self,
        model_name: Optional[str] = None,
        dim: Optional[int] = None,
    ) -> None:
        # 允许注入（单测或后续动态切换）；默认从 settings 读。
        settings = get_settings()
        self._model_name = model_name or settings.embedding_model or DEFAULT_MODEL_NAME
        self._dim = dim if dim is not None else (settings.embedding_dim or BGE_M3_DIM)
        self._model: Optional[SentenceTransformer] = None
        self._load_lock = threading.Lock()

    @property
    def model_name(self) -> str:
        """已配置的 sentence-transformers 模型名（首次 embed 前可读）。"""
        return self._model_name

    @property
    def dim(self) -> int:
        """Embedding 向量维度（与 ``settings.embedding_dim`` 对齐）。"""
        return self._dim

    def _ensure_model(self) -> SentenceTransformer:
        """Lazy 加载 sentence-transformers 模型（线程安全、单例）。

        ``device="cpu"`` 强制 CPU 推理：Apple Silicon 上 PyTorch 的 MPS
        后端存在随机 SIGSEGV（``MetalShaderLibrary`` 哈希表并发竞态，
        crash 报告可见），会直接杀死进程。CPU 推理对本项目的短文档
        embedding 足够快，且与 ``run_in_executor`` 的 CPU 线程池策略一致。
        """
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is None:
                self._model = SentenceTransformer(self._model_name, device="cpu")
        return self._model

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """把 ``texts`` 编码为 ``List[List[float]]``。

        空列表直接返回 ``[]``，避免触发 sentence-transformers 的特殊路径。
        """
        if not texts:
            return []
        model = self._ensure_model()
        # ``convert_to_numpy=True`` 是默认；显式写出便于阅读。
        embeddings = model.encode(
            texts,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        # numpy.ndarray → Python list[float]（JSON / Milvus 友好）
        return [vector.tolist() for vector in embeddings]
