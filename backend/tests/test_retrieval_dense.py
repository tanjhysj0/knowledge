"""#66：DenseRetriever 单测（mock embedding provider + mock vector store）。

覆盖：正常命中、阈值过滤、embedding/向量库异常降级、mock provider
短路、空 query。
"""
from unittest.mock import MagicMock, patch

import pytest

from app.services.retrieval.dense import SCORE_THRESHOLD, DenseRetriever


def _retriever(vector_store) -> DenseRetriever:
    return DenseRetriever(vector_store=vector_store)


def _provider(vectors=None, is_mock=False):
    provider = MagicMock()
    provider.is_mock = is_mock
    provider.embed_texts = MagicMock(return_value=vectors or [[0.1, 0.2]])
    return provider


def _store(rows=None):
    store = MagicMock()
    store.search = MagicMock(return_value=rows or [])
    return store


class TestDenseRetriever:
    @pytest.mark.asyncio
    async def test_returns_hits_above_threshold(self):
        store = _store(
            [
                {"document_id": 1, "chunk_index": 2, "content": "c2", "distance": 0.8},
                {"document_id": 1, "chunk_index": 3, "content": "c3", "distance": 0.4},
            ]
        )
        with patch("app.services.retrieval.dense.get_embedding_provider") as factory:
            factory.return_value = _provider()
            hits = await _retriever(store).retrieve("Q", [1], top_k=5)

        assert len(hits) == 1  # 0.4 低于阈值被过滤
        assert hits[0].chunk_index == 2
        assert hits[0].content == "c2"
        assert hits[0].strategy == "dense"
        assert hits[0].score == 0.8
        store.search.assert_called_once_with(
            query_embedding=[0.1, 0.2], limit=5, document_ids=[1]
        )

    @pytest.mark.asyncio
    async def test_keeps_hit_without_distance(self):
        """distance 缺失时保留（score 记 0，由后续融合兜底）。"""
        store = _store([{"document_id": 1, "chunk_index": 0, "content": "c"}])
        with patch("app.services.retrieval.dense.get_embedding_provider") as factory:
            factory.return_value = _provider()
            hits = await _retriever(store).retrieve("Q", None)

        assert len(hits) == 1
        assert hits[0].score == 0.0

    @pytest.mark.asyncio
    async def test_embedding_failure_degrades_to_empty(self):
        store = _store()
        with patch(
            "app.services.retrieval.dense.get_embedding_provider",
            side_effect=RuntimeError("embedding down"),
        ):
            hits = await _retriever(store).retrieve("Q", None)
        assert hits == []
        store.search.assert_not_called()

    @pytest.mark.asyncio
    async def test_vector_store_failure_degrades_to_empty(self):
        store = _store()
        store.search.side_effect = RuntimeError("milvus down")
        with patch("app.services.retrieval.dense.get_embedding_provider") as factory:
            factory.return_value = _provider()
            hits = await _retriever(store).retrieve("Q", None)
        assert hits == []

    @pytest.mark.asyncio
    async def test_mock_provider_short_circuits(self):
        """mock embedding provider（零向量）短路，避免 Milvus 误命中。"""
        store = _store()
        with patch("app.services.retrieval.dense.get_embedding_provider") as factory:
            factory.return_value = _provider(is_mock=True)
            hits = await _retriever(store).retrieve("Q", None)
        assert hits == []
        store.search.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self):
        store = _store()
        with patch("app.services.retrieval.dense.get_embedding_provider") as factory:
            factory.return_value = _provider()
            assert await _retriever(store).retrieve("", None) == []
            assert await _retriever(store).retrieve("   ", None) == []
        store.search.assert_not_called()

    def test_score_threshold_constant(self):
        """阈值来自旧 _search_chunks 迁移，保持 0.5。"""
        assert SCORE_THRESHOLD == 0.5
