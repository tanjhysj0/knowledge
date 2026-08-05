from typing import List, Dict, Any, Optional
from pymilvus import MilvusClient, DataType
from app.core.config import get_settings

settings = get_settings()


class VectorStoreService:
    """Service for managing Milvus vector storage."""

    def __init__(self):
        self._client: Optional[MilvusClient] = None
        self._collection_name = settings.milvus_collection

    @property
    def client(self) -> MilvusClient:
        """Lazy initialization of Milvus client."""
        if self._client is None:
            self._client = MilvusClient(
                uri=f"http://{settings.milvus_host}:{settings.milvus_port}"
            )
        return self._client

    def _ensure_collection(self) -> None:
        """Ensure the collection exists with proper schema."""
        if self.client.has_collection(self._collection_name):
            return

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
            field_name="embedding", datatype=DataType.FLOAT_VECTOR, dim=1536
        )

        index_params = self.client.prepare_index_params()
        index_params.add_index(
            field_name="embedding",
            index_type="AUTOINDEX",
            metric_type="COSINE",
        )

        self.client.create_collection(
            collection_name=self._collection_name,
            schema=schema,
            index_params=index_params,
        )

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
