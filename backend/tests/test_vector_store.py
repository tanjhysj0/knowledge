"""Unit tests for ``VectorStoreService`` dimension-adaptive behavior (#31).

Milvus 真实 client 在 CI 里没有，因此用 ``MagicMock`` 替代
``pymilvus.MilvusClient``。测试聚焦：

- ``_ensure_collection`` 按 ``self._dim`` 创建新 collection
- 已有 collection 维度不匹配 → drop + 重建
- 已有 collection 维度匹配 → 不动
- ``describe_collection`` 失败或缺 dim 字段 → 保守重建
- 构造函数 ``dim`` 参数优先于 embedding provider / settings
- 构造函数未传 ``dim`` 时从 embedding provider 读取
"""

from unittest.mock import MagicMock, patch

import pytest

from app.services import vector_store as vector_store_module
from app.services.vector_store import (
    EMBEDDING_FIELD_NAME,
    FALLBACK_DIM,
    VectorStoreService,
)


def _make_mock_client(*, has_collection: bool, description: dict | None) -> MagicMock:
    """构造一个 ``MilvusClient`` 替身，绑定指定 ``has_collection`` / ``describe_collection`` 返回值。"""
    client = MagicMock(name="FakeMilvusClient")
    client.has_collection = MagicMock(return_value=has_collection)
    client.describe_collection = MagicMock(return_value=description or {})
    client.drop_collection = MagicMock()
    client.create_collection = MagicMock()
    client.prepare_index_params = MagicMock(return_value=MagicMock(name="FakeIndexParams"))
    return client


def _description_with_dim(dim: int) -> dict:
    """构造 ``describe_collection`` 返回值（pymilvus 2.4 风格）。"""
    return {
        "fields": [
            {"name": "id", "type": "INT64", "params": {"is_primary": True}},
            {"name": "document_id", "type": "INT64", "params": {}},
            {"name": "chunk_index", "type": "INT32", "params": {}},
            {"name": "content", "type": "VARCHAR", "params": {"max_length": 65535}},
            {"name": EMBEDDING_FIELD_NAME, "type": "FLOAT_VECTOR", "params": {"dim": dim}},
        ]
    }


def _description_with_dim_in_type_params(dim: int) -> dict:
    """pymilvus 某些版本把 dim 放在 type_params。"""
    return {
        "fields": [
            {"name": EMBEDDING_FIELD_NAME, "type": "FLOAT_VECTOR", "type_params": {"dim": dim}},
        ]
    }


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


class TestEnsureCollection:
    """``_ensure_collection`` dim 自适应行为。"""

    def test_creates_collection_when_missing_with_explicit_dim(self):
        """collection 不存在 → 用 ``self._dim`` 创建。"""
        client = _make_mock_client(has_collection=False, description=None)
        service = VectorStoreService.__new__(VectorStoreService)
        service._client = client
        service._collection_name = "test_collection"
        service._dim = 384

        service._ensure_collection()

        # 验证 create_collection 被调用一次
        client.create_collection.assert_called_once()
        # 验证 schema 中 embedding 字段 dim == 384
        call_kwargs = client.create_collection.call_args.kwargs
        schema = call_kwargs["schema"]
        embedding_field = next(
            f for f in schema.fields if f.name == EMBEDDING_FIELD_NAME
        )
        assert embedding_field.dim == 384
        # 没 drop
        client.drop_collection.assert_not_called()

    def test_keeps_collection_when_dim_matches(self):
        """已有 collection 维度匹配 → 不动。"""
        description = _description_with_dim(dim=1024)
        client = _make_mock_client(has_collection=True, description=description)
        service = VectorStoreService.__new__(VectorStoreService)
        service._client = client
        service._collection_name = "test_collection"
        service._dim = 1024

        service._ensure_collection()

        client.drop_collection.assert_not_called()
        client.create_collection.assert_not_called()

    def test_drops_and_recreates_when_dim_mismatches(self):
        """已有 collection 维度不匹配 → drop + 重建。"""
        description = _description_with_dim(dim=1536)  # 旧 dim
        client = _make_mock_client(has_collection=True, description=description)
        service = VectorStoreService.__new__(VectorStoreService)
        service._client = client
        service._collection_name = "test_collection"
        service._dim = 1024  # 新 dim

        service._ensure_collection()

        # drop 旧 → 重建
        client.drop_collection.assert_called_once_with("test_collection")
        client.create_collection.assert_called_once()
        # 验证新 schema dim == 1024
        call_kwargs = client.create_collection.call_args.kwargs
        schema = call_kwargs["schema"]
        embedding_field = next(
            f for f in schema.fields if f.name == EMBEDDING_FIELD_NAME
        )
        assert embedding_field.dim == 1024

    def test_reads_dim_from_type_params_variant(self):
        """pymilvus 某些版本把 dim 放在 ``type_params`` 而不是 ``params``。"""
        description = _description_with_dim_in_type_params(dim=768)
        client = _make_mock_client(has_collection=True, description=description)
        service = VectorStoreService.__new__(VectorStoreService)
        service._client = client
        service._collection_name = "test_collection"
        service._dim = 1024  # 不一致 → 重建

        service._ensure_collection()

        client.drop_collection.assert_called_once()
        client.create_collection.assert_called_once()

    def test_drops_and_recreates_when_dim_field_missing(self):
        """``describe_collection`` 找不到 embedding dim → 保守重建。"""
        client = _make_mock_client(
            has_collection=True,
            description={"fields": [{"name": "id", "type": "INT64"}]},
        )
        service = VectorStoreService.__new__(VectorStoreService)
        service._client = client
        service._collection_name = "test_collection"
        service._dim = 1024

        service._ensure_collection()

        client.drop_collection.assert_called_once()
        client.create_collection.assert_called_once()

    def test_drops_and_recreates_when_describe_raises(self):
        """``describe_collection`` 抛异常 → 不阻塞，重建。"""
        client = MagicMock(name="FakeMilvusClient")
        client.has_collection = MagicMock(return_value=True)
        client.describe_collection = MagicMock(side_effect=RuntimeError("rpc fail"))
        client.drop_collection = MagicMock()
        client.create_collection = MagicMock()
        client.prepare_index_params = MagicMock(return_value=MagicMock(name="FakeIndexParams"))
        service = VectorStoreService.__new__(VectorStoreService)
        service._client = client
        service._collection_name = "test_collection"
        service._dim = 1024

        service._ensure_collection()

        client.drop_collection.assert_called_once()
        client.create_collection.assert_called_once()

    def test_create_collection_includes_index_params(self):
        """创建 collection 时必须配置向量索引（保证后续 search 性能）。"""
        client = _make_mock_client(has_collection=False, description=None)
        service = VectorStoreService.__new__(VectorStoreService)
        service._client = client
        service._collection_name = "test_collection"
        service._dim = 1024

        service._ensure_collection()

        client.prepare_index_params.assert_called_once()
        # index_params.add_index 必须被调用且指定 embedding 字段
        index_params = client.prepare_index_params.return_value
        assert index_params.add_index.called
        call_kwargs = index_params.add_index.call_args.kwargs
        assert call_kwargs["field_name"] == EMBEDDING_FIELD_NAME
        assert call_kwargs["metric_type"] == "COSINE"


class TestVectorStoreInsertAndSearch:
    """回归测试：insert / search / delete_by_document_id 仍走 dim 一致路径。"""

    def test_insert_calls_ensure_collection_first(self):
        """``insert`` 必须先确保 collection 存在（按 self._dim 创建）。"""
        client = _make_mock_client(has_collection=False, description=None)
        service = VectorStoreService.__new__(VectorStoreService)
        service._client = client
        service._collection_name = "test_collection"
        service._dim = 1024

        service.insert(document_id=1, chunks=["c"], embeddings=[[0.1] * 1024])

        # 第一次 ensure_collection 会触发 create_collection
        client.create_collection.assert_called_once()
        # insert 必须发生
        client.insert.assert_called_once()
        insert_kwargs = client.insert.call_args.kwargs
        assert insert_kwargs["collection_name"] == "test_collection"
        assert len(insert_kwargs["data"]) == 1
        assert insert_kwargs["data"][0]["document_id"] == 1
        assert len(insert_kwargs["data"][0]["embedding"]) == 1024

    def test_search_returns_empty_when_collection_missing(self):
        """collection 不存在时 search 返回 ``[]``（不触发 ensure_collection）。"""
        client = _make_mock_client(has_collection=False, description=None)
        service = VectorStoreService.__new__(VectorStoreService)
        service._client = client
        service._collection_name = "test_collection"
        service._dim = 1024

        result = service.search(query_embedding=[0.1] * 1024, limit=5)

        assert result == []
        # 不应触发 create_collection（search 是只读路径）
        client.create_collection.assert_not_called()
