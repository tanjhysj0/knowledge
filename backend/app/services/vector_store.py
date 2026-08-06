from typing import Any, Dict, List, Optional
from pymilvus import MilvusClient, DataType
from app.core.config import get_settings
from app.services.embedding import get_embedding_provider

settings = get_settings()

EMBEDDING_FIELD_NAME = "embedding"
FALLBACK_DIM = 1024  # 当 collection 描述中无法读到 dim 时的安全默认值


class VectorStoreService:
    """Service for managing Milvus vector storage.

    集合维度自适应：首次访问时会比较已有 collection 的 ``embedding`` 字段
    维度与当前 ``settings.embedding_dim``，不一致时 drop 后按新 dim 重建
    （重建会清空数据，应在重跑文档上传脚本后使用）。
    """

    def __init__(self, dim: Optional[int] = None):
        self._client: Optional[MilvusClient] = None
        self._collection_name = settings.milvus_collection
        # dim 可在构造时注入（单测/热更新）；默认从 embedding provider 读
        if dim is None:
            try:
                dim = get_embedding_provider().dim
            except Exception:  # noqa: BLE001 — embedding provider 不可用时回退到 settings
                dim = settings.embedding_dim or FALLBACK_DIM
        self._dim = dim

    @property
    def client(self) -> MilvusClient:
        """Lazy initialization of Milvus client."""
        if self._client is None:
            self._client = MilvusClient(
                uri=f"http://{settings.milvus_host}:{settings.milvus_port}"
            )
        return self._client

    @property
    def dim(self) -> int:
        """当前 collection 使用的向量维度。"""
        return self._dim

    def _collection_dim(self, description: Dict[str, Any]) -> Optional[int]:
        """从 ``describe_collection`` 返回的 dict 里挑出 ``embedding`` 字段维度。"""
        for field in description.get("fields", []) or []:
            if field.get("name") == EMBEDDING_FIELD_NAME:
                params = field.get("params") or {}
                dim = params.get("dim")
                if dim is not None:
                    return int(dim)
                # 某些 pymilvus 版本把 dim 放在 type_params
                type_params = field.get("type_params") or {}
                dim = type_params.get("dim")
                if dim is not None:
                    return int(dim)
        return None

    def _create_collection(self, dim: int) -> None:
        """按 ``dim`` 创建空 collection + 向量索引。"""
        schema = MilvusClient.create_schema(
            auto_id=True,
            enable_dynamic_field=True,
        )

        schema.add_field(field_name="id", datatype=DataType.INT64, is_primary=True)
        schema.add_field(
            field_name="document_id", datatype=DataType.INT64
        )
        schema.add_field(
            field_name="chunk_index", datatype=DataType.INT32
        )
        schema.add_field(
            field_name="content", datatype=DataType.VARCHAR, max_length=65535
        )
        schema.add_field(
            field_name=EMBEDDING_FIELD_NAME, datatype=DataType.FLOAT_VECTOR, dim=dim
        )

        index_params = self.client.prepare_index_params()
        index_params.add_index(
            field_name=EMBEDDING_FIELD_NAME,
            index_type="AUTOINDEX",
            metric_type="COSINE",
        )

        self.client.create_collection(
            collection_name=self._collection_name,
            schema=schema,
            index_params=index_params,
        )

    def _ensure_collection(self) -> None:
        """确保 collection 存在，且 ``embedding`` 维度与 ``self._dim`` 一致。

        行为：

        1. collection 不存在 → 创建（使用 ``self._dim``）。
        2. collection 存在 + 维度匹配 → 不动。
        3. collection 存在 + 维度不匹配 → drop 后重建（使用 ``self._dim``）。

        dim 变化会清空数据，调用方需要自行决定是否重跑文档上传脚本。
        """
        if not self.client.has_collection(self._collection_name):
            self._create_collection(self._dim)
            return

        try:
            description = self.client.describe_collection(self._collection_name)
        except Exception:  # noqa: BLE001 — 老版本 SDK / collection 描述失败
            description = {}

        existing_dim = self._collection_dim(description or {})
        if existing_dim is None:
            # 描述里没找到 dim（可能 schema 异常）→ 保守重建
            self.client.drop_collection(self._collection_name)
            self._create_collection(self._dim)
            return

        if existing_dim != self._dim:
            # 维度不匹配 → drop + 重建
            self.client.drop_collection(self._collection_name)
            self._create_collection(self._dim)

    def insert(
        self,
        document_id: int,
        chunks: List[str],
        embeddings: List[List[float]],
    ) -> List[int]:
        """Insert document chunks with embeddings into Milvus."""
        self._ensure_collection()

        data = []
        for idx, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            data.append({
                "document_id": document_id,
                "chunk_index": idx,
                "content": chunk,
                "embedding": embedding,
            })

        result = self.client.insert(
            collection_name=self._collection_name,
            data=data,
        )
        return result.get("ids", [])

    def delete_by_document_id(self, document_id: int) -> None:
        """Delete all chunks for a document."""
        if not self.client.has_collection(self._collection_name):
            return
        
        self.client.delete(
            collection_name=self._collection_name,
            filter=f"document_id == {document_id}",
        )

    def search(
        self,
        query_embedding: List[float],
        limit: int = 5,
        document_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """Search for similar chunks."""
        if not self.client.has_collection(self._collection_name):
            return []

        filter_expr = None
        if document_ids:
            doc_ids_str = ", ".join(str(d) for d in document_ids)
            filter_expr = f"document_id in [{doc_ids_str}]"

        results = self.client.search(
            collection_name=self._collection_name,
            data=[query_embedding],
            limit=limit,
            filter=filter_expr,
            output_fields=["document_id", "chunk_index", "content"],
        )

        return [
            {
                "document_id": hit.get("entity", {}).get("document_id"),
                "chunk_index": hit.get("entity", {}).get("chunk_index"),
                "content": hit.get("entity", {}).get("content"),
                "distance": hit.get("distance"),
            }
            for hit in results[0]
        ]
