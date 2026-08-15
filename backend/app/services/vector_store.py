"""#71：dense 向量存储——PG/pgvector 实现（取代 Milvus）。

保持 ``insert`` / ``delete_by_document_id`` / ``search`` 公开契约不变：
检索在 PG 内用 pgvector ``<=>`` 算子做 COSINE 相似度搜索，返回的
``distance`` 字段实为余弦相似度（越大越相关），与迁移前 pymilvus
COSINE ``distance`` 语义一致，上层阈值过滤无需改动。

同步接口（psycopg2 独立连接）：调用方（DenseRetriever、documents 后台
索引）在 executor 线程中调用，不阻塞事件循环。

#71 新增全文存储：``save_document_text`` 把解析后的全文写入
``document_texts`` 表，``delete_by_document_id`` 级联清理全文与向量。
"""
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    Table,
    Text,
    create_engine,
    delete,
    inspect,
    select,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models.retrieval_index import DocumentText, VectorChunk
from app.services.embedding import get_embedding_provider

settings = get_settings()

EMBEDDING_FIELD_NAME = "embedding"  # 向量列名（维度自适应检查用）
FALLBACK_DIM = 1024  # 当 embedding provider 与 settings 都读不到 dim 时的安全默认值

CHUNKS_TABLE = VectorChunk.__tablename__
TEXTS_TABLE = DocumentText.__tablename__
INDEX_NAME = "ix_vector_chunks_embedding"

# 模块级同步引擎单例：VectorStoreService 按操作创建，每个实例各自
# create_engine 会让连接池生命周期失控（连接靠 GC 兑底关闭、高并发
# 反复新建连接逼近 max_connections）。单例引擎随进程存续，连接复用。
_sync_engine = None
_sync_engine_lock = threading.Lock()


def _get_sync_engine():
    global _sync_engine
    if _sync_engine is None:
        with _sync_engine_lock:
            if _sync_engine is None:
                _sync_engine = create_engine(
                    settings.sync_database_url,
                    pool_pre_ping=True,
                    # 与 async 引擎对齐：定期重建连接，防止长生命周期
                    # 进程持有过期连接。
                    pool_recycle=3600,
                )
    return _sync_engine


class VectorStoreService:
    """Service for managing PG/pgvector dense vector storage (#71).

    表维度自适应：首次写入时会比较已有 ``vector_chunks`` 表的 ``embedding``
    列维度与当前 ``self._dim``，不一致时 drop 后按新 dim 重建（重建会清空
    数据，应在重跑文档上传脚本后使用）。
    """

    def __init__(self, dim: Optional[int] = None):
        self._engine = None
        self._session_factory = None
        # dim 可在构造时注入（单测/热更新）；默认从 embedding provider 读
        if dim is None:
            try:
                dim = get_embedding_provider().dim
            except Exception:  # noqa: BLE001 — embedding provider 不可用时回退到 settings
                dim = settings.embedding_dim or FALLBACK_DIM
        self._dim = dim

    @property
    def dim(self) -> int:
        """当前向量存储使用的向量维度。"""
        return self._dim

    @property
    def engine(self):
        """同步 engine（psycopg2 驱动 ``postgresql://`` URL）。

        实例级 ``_engine`` 注入（单测）优先；生产环境走模块级单例
        :func:`_get_sync_engine` 复用连接，避免每次操作新建连接池。
        """
        if self._engine is not None:
            return self._engine
        return _get_sync_engine()

    def _session(self):
        if self._session_factory is None:
            self._session_factory = sessionmaker(bind=self.engine)
        return self._session_factory()

    def _table_exists(self, table_name: str) -> bool:
        return inspect(self.engine).has_table(table_name)

    def _table_dim(self) -> Optional[int]:
        """从 PG 目录读出 ``vector_chunks.embedding`` 列的实际向量维度。"""
        with self.engine.connect() as conn:
            row = conn.exec_driver_sql(
                "SELECT format_type(atttypid, atttypmod) "
                "FROM pg_attribute "
                f"WHERE attrelid = '{CHUNKS_TABLE}'::regclass "
                f"AND attname = '{EMBEDDING_FIELD_NAME}'"
            ).first()
        return self._parse_dim(row[0]) if row else None

    def _create_chunks_table(self, dim: int) -> None:
        """按 ``dim`` 动态建 ``vector_chunks`` 表 + HNSW/COSINE 索引。

        列集合与 ORM :class:`VectorChunk` 保持一致（含 ``created_at``），
        否则 ORM bulk insert 会引用不存在的列。
        """
        table = Table(
            CHUNKS_TABLE,
            MetaData(),
            Column("id", Integer, primary_key=True),
            Column("document_id", Integer, nullable=False, index=True),
            Column("chunk_index", Integer, nullable=False),
            Column("content", Text, nullable=False),
            Column(EMBEDDING_FIELD_NAME, Vector(dim), nullable=False),
            Column("created_at", DateTime, default=datetime.utcnow),
        )
        table.create(self.engine, checkfirst=True)
        with self.engine.begin() as conn:
            conn.exec_driver_sql(
                f"CREATE INDEX IF NOT EXISTS {INDEX_NAME} "
                f"ON {CHUNKS_TABLE} USING hnsw (embedding vector_cosine_ops)"
            )

    def _ensure_chunks_table(self) -> None:
        """确保 ``vector_chunks`` 存在，且 ``embedding`` 维度与 ``self._dim`` 一致。

        行为：

        1. 表不存在 → 创建（使用 ``self._dim``）。
        2. 表存在 + 维度匹配 → 不动。
        3. 表存在 + 维度不匹配 → drop 后重建（使用 ``self._dim``）。

        dim 变化会清空数据，调用方需要自行决定是否重跑文档上传脚本。
        """
        if not self._table_exists(CHUNKS_TABLE):
            self._create_chunks_table(self._dim)
            return

        existing_dim = self._table_dim()
        if existing_dim is None or existing_dim != self._dim:
            # 描述失败 / 维度不匹配 → drop + 重建
            with self.engine.begin() as conn:
                conn.exec_driver_sql(f"DROP TABLE IF EXISTS {CHUNKS_TABLE}")
            self._create_chunks_table(self._dim)

    def _parse_dim(self, type_spec: str) -> Optional[int]:
        """从 ``format_type`` 输出（如 ``vector(1024)``）解析维度。"""
        if not type_spec or not type_spec.startswith("vector("):
            return None
        try:
            return int(type_spec[len("vector("):-1])
        except ValueError:
            return None

    def insert(
        self,
        document_id: int,
        chunks: List[str],
        embeddings: List[List[float]],
    ) -> List[int]:
        """Insert document chunks with embeddings into PG ``vector_chunks``."""
        self._ensure_chunks_table()

        rows = [
            {
                "document_id": document_id,
                "chunk_index": idx,
                "content": chunk,
                EMBEDDING_FIELD_NAME: embedding,
            }
            for idx, (chunk, embedding) in enumerate(zip(chunks, embeddings))
        ]
        if not rows:
            return []

        with self._session() as session:
            result = session.execute(
                pg_insert(VectorChunk)
                .values(rows)
                .returning(VectorChunk.id)
            )
            ids = list(result.scalars())
            session.commit()
        return ids

    def save_document_text(self, document_id: int, full_text: str) -> None:
        """#71：写入/更新小说的解析全文（幂等 upsert，每本小说一行）。"""
        if not self._table_exists(TEXTS_TABLE):
            DocumentText.__table__.create(self.engine, checkfirst=True)

        with self._session() as session:
            stmt = pg_insert(DocumentText).values(
                document_id=document_id, full_text=full_text
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["document_id"],
                set_={"full_text": full_text},
            )
            session.execute(stmt)
            session.commit()

    def has_vectors(self, document_id: int) -> bool:
        """#72：判断小说是否已有 dense 向量（重建脚本按此跳过）。

        表不存在视为无向量；已删除/半写入的残留行与正常向量同判。
        """
        if not self._table_exists(CHUNKS_TABLE):
            return False

        with self._session() as session:
            result = session.execute(
                select(VectorChunk.id)
                .where(VectorChunk.document_id == document_id)
                .limit(1)
            )
            return result.first() is not None

    def delete_by_document_id(self, document_id: int) -> None:
        """Delete all chunks and the stored full text for a document (#71).

        两表各自独立判断存在性：即使 ``vector_chunks`` 尚未建立（如
        解析成功但向量写入前失败），``document_texts`` 中的全文仍要
        清理，避免孤儿数据。
        """
        with self._session() as session:
            if self._table_exists(CHUNKS_TABLE):
                session.execute(
                    delete(VectorChunk).where(
                        VectorChunk.document_id == document_id
                    )
                )
            if self._table_exists(TEXTS_TABLE):
                session.execute(
                    delete(DocumentText).where(
                        DocumentText.document_id == document_id
                    )
                )
            session.commit()

    def search(
        self,
        query_embedding: List[float],
        limit: int = 5,
        document_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """Search for similar chunks via pgvector COSINE similarity."""
        if not self._table_exists(CHUNKS_TABLE):
            return []

        cosine_distance = VectorChunk.embedding.cosine_distance(query_embedding)
        # pgvector ``<=>`` 返回余弦距离（越小越近）；转回相似度保持
        # ``distance`` 越大越相关的既有契约。
        similarity = 1 - cosine_distance

        stmt = select(
            VectorChunk.document_id,
            VectorChunk.chunk_index,
            VectorChunk.content,
            similarity.label("similarity"),
        ).order_by(cosine_distance)

        if document_ids:
            stmt = stmt.where(VectorChunk.document_id.in_(document_ids))

        stmt = stmt.limit(limit)

        with self._session() as session:
            rows = session.execute(stmt).all()

        return [
            {
                "document_id": row.document_id,
                "chunk_index": row.chunk_index,
                "content": row.content,
                "distance": float(row.similarity),
            }
            for row in rows
        ]
