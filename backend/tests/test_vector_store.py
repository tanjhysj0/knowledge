"""Unit tests for ``VectorStoreService`` PG/pgvector implementation (#71).

真实 PG 在 CI 里没有，因此用 ``MagicMock`` 替代同步 engine / session。
测试聚焦：

- ``_ensure_chunks_table`` 按 ``self._dim`` 创建新表（含 HNSW/COSINE 索引）
- 已有表维度不匹配 → drop + 重建
- 已有表维度匹配 → 不动
- ``_table_dim`` 从 ``format_type`` 输出解析维度
- 构造函数 ``dim`` 参数优先于 embedding provider / settings
- 构造函数未传 ``dim`` 时从 embedding provider 读取
- insert / search / delete_by_document_id / save_document_text 行为
"""
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import Table as RealTable

from app.services import vector_store as vector_store_module
from app.services.vector_store import (
    CHUNKS_TABLE,
    EMBEDDING_FIELD_NAME,
    FALLBACK_DIM,
    TEXTS_TABLE,
    VectorStoreService,
)


class _FakeConn:
    """记录 exec_driver_sql 的假连接（``connect()`` / ``begin()`` 共用）。"""

    def __init__(self, dim_result=None):
        self._dim_result = dim_result
        self.driver_sqls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def exec_driver_sql(self, sql):
        self.driver_sqls.append(sql)
        result = MagicMock()
        result.first.return_value = (self._dim_result,) if self._dim_result else None
        return result


class _FakeEngine:
    """最小假同步 engine：``connect`` 返回带维度结果的连接。"""

    def __init__(self, dim_result=None):
        self.dim_result = dim_result
        self.begin_conn = _FakeConn()

    def connect(self):
        return _FakeConn(self.dim_result)

    def begin(self):
        return self.begin_conn


class _FakeSession:
    """最小假同步 session：记录 execute 语句与 commit。"""

    def __init__(self, rows=None, ids=None, first_result=None):
        self._rows = rows or []
        self._ids = ids or []
        self._first_result = first_result
        self.statements = []
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, stmt, *args, **kwargs):
        self.statements.append(stmt)
        result = MagicMock()
        result.scalars.return_value = iter(self._ids)
        result.all.return_value = self._rows
        result.first.return_value = self._first_result
        return result

    def commit(self):
        self.commits += 1


def _make_service(
    *,
    dim=1024,
    table_dim="vector(1024)",
    tables=(CHUNKS_TABLE, TEXTS_TABLE),
    session=None,
) -> tuple[VectorStoreService, _FakeEngine, _FakeSession]:
    """构造一个注入假 engine/session 的 VectorStoreService。"""
    service = VectorStoreService.__new__(VectorStoreService)
    service._dim = dim
    service._engine = _FakeEngine(dim_result=table_dim)
    service._session_factory = lambda: session or _FakeSession()
    service._tables = set(tables)
    return service, service._engine, (session or _FakeSession())


def _patch_has_table(service):
    """让 ``_table_exists`` 按 ``service._tables`` 判定。"""
    inspector = MagicMock()
    inspector.has_table = MagicMock(
        side_effect=lambda name: name in service._tables
    )
    return patch.object(
        vector_store_module, "inspect", return_value=inspector
    )


def _create_table_spy():
    """捕获 ``_create_chunks_table`` 构造的表定义（真实 Table + 假 create）。"""
    created = []

    def _factory(name, metadata, *cols):
        table = RealTable(name, metadata, *cols)
        table.create = MagicMock()
        created.append(table)
        return table

    return created, patch.object(
        vector_store_module, "Table", side_effect=_factory
    )


class TestVectorStoreDimConstructor:
    """``VectorStoreService(dim=...)`` 行为。"""

    def test_explicit_dim_takes_precedence_over_embedding_provider(self):
        """构造函数显式 ``dim`` 优先于 embedding provider。"""
        with patch.object(vector_store_module, "get_embedding_provider") as mock_gs:
            mock_provider = MagicMock()
            mock_provider.dim = 999
            mock_gs.return_value = mock_provider
            service = VectorStoreService(dim=384)
        assert service.dim == 384

    def test_falls_back_to_embedding_provider_when_dim_omitted(self):
        """``dim`` 未传时从 ``get_embedding_provider().dim`` 读。"""
        with patch.object(vector_store_module, "get_embedding_provider") as mock_gs:
            mock_provider = MagicMock()
            mock_provider.dim = 512
            mock_gs.return_value = mock_provider
            service = VectorStoreService()
        assert service.dim == 512

    def test_falls_back_to_settings_when_embedding_provider_raises(self):
        """embedding provider 抛异常时回退到 ``settings.embedding_dim``。"""
        with patch.object(
            vector_store_module, "get_embedding_provider", side_effect=RuntimeError("boom")
        ):
            with patch.object(vector_store_module.settings, "embedding_dim", 256):
                service = VectorStoreService()
        assert service.dim == 256

    def test_uses_safe_default_when_both_fail(self):
        """embedding provider + settings 都不可用时使用 ``FALLBACK_DIM``。"""
        with patch.object(
            vector_store_module, "get_embedding_provider", side_effect=RuntimeError("boom")
        ):
            with patch.object(vector_store_module.settings, "embedding_dim", 0):
                service = VectorStoreService()
        assert service.dim == FALLBACK_DIM


class TestParseDim:
    """``_parse_dim`` 解析 ``format_type`` 输出。"""

    def _service(self):
        service = VectorStoreService.__new__(VectorStoreService)
        service._dim = 1024
        return service

    def test_parses_vector_dim(self):
        assert self._service()._parse_dim("vector(1024)") == 1024

    def test_returns_none_for_unknown_format(self):
        assert self._service()._parse_dim("integer") is None
        assert self._service()._parse_dim("") is None
        assert self._service()._parse_dim(None) is None


class TestEnsureChunksTable:
    """``_ensure_chunks_table`` dim 自适应行为。"""

    def test_creates_table_when_missing_with_hnsw_index(self):
        """表不存在 → 用 ``self._dim`` 建表 + HNSW/COSINE 索引。"""
        service, engine, _ = _make_service(dim=384, tables=())
        created, spy = _create_table_spy()

        with _patch_has_table(service), spy:
            service._ensure_chunks_table()

        # 建表 dim == 384，且 embedding 列在表中
        assert len(created) == 1
        embedding_col = created[0].c[EMBEDDING_FIELD_NAME]
        assert embedding_col.type.dim == 384
        # 索引 SQL 使用 hnsw + vector_cosine_ops
        index_sqls = [
            sql for sql in engine.begin_conn.driver_sqls if "CREATE INDEX" in sql
        ]
        assert len(index_sqls) == 1
        assert "USING hnsw" in index_sqls[0]
        assert "vector_cosine_ops" in index_sqls[0]

    def test_keeps_table_when_dim_matches(self):
        """已有表维度匹配 → 不动。"""
        service, engine, _ = _make_service(dim=1024, table_dim="vector(1024)")
        created, spy = _create_table_spy()

        with _patch_has_table(service), spy:
            service._ensure_chunks_table()

        assert created == []  # 未重建
        assert all(
            "DROP TABLE" not in sql for sql in engine.begin_conn.driver_sqls
        )

    def test_drops_and_recreates_when_dim_mismatches(self):
        """已有表维度不匹配 → drop + 重建（用 ``self._dim``）。"""
        service, engine, _ = _make_service(dim=1024, table_dim="vector(1536)")
        created, spy = _create_table_spy()

        with _patch_has_table(service), spy:
            service._ensure_chunks_table()

        assert len(created) == 1
        assert created[0].c[EMBEDDING_FIELD_NAME].type.dim == 1024
        drop_sqls = [
            sql for sql in engine.begin_conn.driver_sqls if "DROP TABLE" in sql
        ]
        assert len(drop_sqls) == 1
        assert CHUNKS_TABLE in drop_sqls[0]

    def test_drops_and_recreates_when_dim_unknown(self):
        """读不到维度（格式异常）→ 保守重建。"""
        service, engine, _ = _make_service(dim=1024, table_dim="integer")
        created, spy = _create_table_spy()

        with _patch_has_table(service), spy:
            service._ensure_chunks_table()

        assert len(created) == 1
        assert any(
            "DROP TABLE" in sql for sql in engine.begin_conn.driver_sqls
        )


class TestInsert:
    """``insert`` 写入 chunks + embeddings。"""

    def test_insert_returns_ids_and_commits(self):
        session = _FakeSession(ids=[11, 12])
        service, _, _ = _make_service(session=session)
        service._ensure_chunks_table = MagicMock()

        ids = service.insert(
            document_id=1, chunks=["c0", "c1"], embeddings=[[0.1] * 4, [0.2] * 4]
        )

        assert ids == [11, 12]
        assert session.commits == 1
        assert len(session.statements) == 1

    def test_insert_skips_empty_rows(self):
        service, _, _ = _make_service()
        service._ensure_chunks_table = MagicMock()

        assert service.insert(document_id=1, chunks=[], embeddings=[]) == []

    def test_insert_ensures_table_first(self):
        service, _, _ = _make_service()
        service._ensure_chunks_table = MagicMock()

        service.insert(
            document_id=1, chunks=["c"], embeddings=[[0.1] * 4]
        )

        service._ensure_chunks_table.assert_called_once()


class TestSearch:
    """``search`` 走 pgvector COSINE 相似度路径。"""

    def _row(self, document_id, chunk_index, content, similarity):
        return MagicMock(
            document_id=document_id,
            chunk_index=chunk_index,
            content=content,
            similarity=similarity,
        )

    def test_search_returns_hits_with_similarity(self):
        rows = [self._row(1, 2, "c2", 0.85)]
        session = _FakeSession(rows=rows)
        service, _, _ = _make_service(session=session)

        with _patch_has_table(service):
            hits = service.search(query_embedding=[0.1] * 4, limit=5)

        assert len(hits) == 1
        assert hits[0] == {
            "document_id": 1,
            "chunk_index": 2,
            "content": "c2",
            "distance": 0.85,
        }

    def test_search_returns_empty_when_table_missing(self):
        service, _, _ = _make_service(tables=())
        with _patch_has_table(service):
            assert service.search(query_embedding=[0.1] * 4) == []

    def test_search_filters_by_document_ids(self):
        session = _FakeSession(rows=[])
        service, _, _ = _make_service(session=session)

        with _patch_has_table(service):
            service.search(
                query_embedding=[0.1] * 4, limit=5, document_ids=[1, 2]
            )

        compiled = str(session.statements[0].compile())
        assert "document_id IN" in compiled

    def test_search_without_document_ids_has_no_filter(self):
        session = _FakeSession(rows=[])
        service, _, _ = _make_service(session=session)

        with _patch_has_table(service):
            service.search(query_embedding=[0.1] * 4, limit=5)

        compiled = str(session.statements[0].compile())
        assert "document_id IN" not in compiled


class TestDeleteByDocumentId:
    """``delete_by_document_id`` 级联清理向量与全文（#71）。"""

    def test_delete_chunks_and_texts(self):
        session = _FakeSession()
        service, _, _ = _make_service(session=session)

        with _patch_has_table(service):
            service.delete_by_document_id(1)

        assert session.commits == 1
        assert len(session.statements) == 2

    def test_delete_texts_even_when_chunks_table_missing(self):
        """vector_chunks 缺失时不跳过全文清理，避免孤儿 document_texts。"""
        session = _FakeSession()
        service, _, _ = _make_service(session=session, tables=(TEXTS_TABLE,))

        with _patch_has_table(service):
            service.delete_by_document_id(1)

        assert session.commits == 1
        assert len(session.statements) == 1
        assert "document_texts" in str(session.statements[0])


class TestSaveDocumentText:
    """``save_document_text`` 幂等 upsert 全文（#71）。"""

    def test_upserts_and_commits(self):
        session = _FakeSession()
        service, _, _ = _make_service(session=session)

        with _patch_has_table(service):
            service.save_document_text(1, "第一章 起源")

        assert session.commits == 1
        assert len(session.statements) == 1

    def test_creates_table_when_missing(self):
        session = _FakeSession()
        service, _, _ = _make_service(session=session, tables=(CHUNKS_TABLE,))

        with _patch_has_table(service):
            with patch(
                "app.services.vector_store.DocumentText.__table__.create"
            ) as mock_create:
                service.save_document_text(1, "全文")

        mock_create.assert_called_once()


class TestHasVectors:
    """``has_vectors`` 幂等判断（#72 重建脚本按此跳过）。"""

    def test_returns_false_when_table_missing(self):
        service, _, _ = _make_service(tables=())

        with _patch_has_table(service):
            assert service.has_vectors(1) is False

    def test_returns_true_when_row_exists(self):
        session = _FakeSession(first_result=MagicMock())
        service, _, _ = _make_service(session=session)

        with _patch_has_table(service):
            assert service.has_vectors(1) is True

        assert len(session.statements) == 1

    def test_returns_false_when_no_rows(self):
        session = _FakeSession(first_result=None)
        service, _, _ = _make_service(session=session)

        with _patch_has_table(service):
            assert service.has_vectors(1) is False
