"""Unit tests for :class:`app.services.rag.RAGService` retrieval behavior (#32).

测试策略
--------
- ``get_embedding_provider`` + ``VectorStoreService.search`` 全 mock 化；
  不调真实 bge-m3 / Milvus
- ``RAGService._llm`` 也 mock，使 answer/answer_stream 不依赖真实 LLM
- 覆盖：检索命中 / 未命中 / 阈值过滤 / sources 去重 / embedding 异常回退 /
  vector_store 异常回退 / 空问题短路 / 异步执行不阻塞事件循环
"""

import asyncio
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.rag import RETRIEVAL_SCORE_THRESHOLD, RAGService


def _make_hit(document_id: int, chunk_index: int, content: str, distance: float) -> dict:
    return {
        "document_id": document_id,
        "chunk_index": chunk_index,
        "content": content,
        "distance": distance,
    }


class TestSearchChunks:
    """``RAGService._search_chunks`` 同步检索。"""

    def _build_service(self, *, vector_hits: list | None = None, search_raises=None) -> tuple[RAGService, MagicMock, MagicMock]:
        """构造 RAGService，并把 embedding/vector_store 都替换为 mock。"""
        # vector store 替身
        vector_store = MagicMock()
        vector_store.search = MagicMock(
            side_effect=search_raises if search_raises else None
        )
        if not search_raises:
            vector_store.search = MagicMock(return_value=vector_hits or [])

        # embedding provider 替身
        embedding_provider = MagicMock()
        embedding_provider.embed_texts = MagicMock(
            return_value=[[0.1] * 1024]  # 单条 query 向量
        )

        rag = RAGService.__new__(RAGService)
        rag._vector_store = vector_store
        rag._request = None

        return rag, embedding_provider, vector_store

    def test_returns_empty_for_blank_question(self):
        rag, _, vector_store = self._build_service()
        assert rag._search_chunks("", [], top_k=5) == []
        assert rag._search_chunks("   ", [], top_k=5) == []
        # 空问题不触发任何下游调用
        vector_store.search.assert_not_called()

    def test_search_returns_hits_with_expected_structure(self):
        hits = [
            _make_hit(1, 0, "chunk A", 0.8),
            _make_hit(1, 1, "chunk B", 0.9),
        ]
        rag, embedding_provider, vector_store = self._build_service(vector_hits=hits)

        with patch(
            "app.services.rag.get_embedding_provider", return_value=embedding_provider
        ):
            results = rag._search_chunks("What is X?", [1, 2], top_k=5)

        assert len(results) == 2
        assert results[0]["document_id"] == 1
        assert results[0]["content"] == "chunk A"
        assert results[0]["distance"] == 0.8
        # embedding 被调用，参数是 [question]
        embedding_provider.embed_texts.assert_called_once_with(["What is X?"])
        # vector_store.search 拿到 query 向量 + document_ids + top_k
        vector_store.search.assert_called_once()
        call_kwargs = vector_store.search.call_args.kwargs
        assert call_kwargs["limit"] == 5
        assert call_kwargs["document_ids"] == [1, 2]
        assert len(call_kwargs["query_embedding"]) == 1024

    def test_filters_out_hits_below_threshold(self):
        """相似度 < ``RETRIEVAL_SCORE_THRESHOLD`` 的命中被过滤。

        pymilvus 对 COSINE metric 的 ``distance`` 字段即相似度（越大越相关）。
        """
        hits = [
            _make_hit(1, 0, "relevant", 0.8),  # 留下
            _make_hit(2, 0, "irrelevant", 0.2),  # 过滤
            _make_hit(3, 0, "borderline", RETRIEVAL_SCORE_THRESHOLD),  # 留下（边界）
        ]
        rag, embedding_provider, _ = self._build_service(vector_hits=hits)

        with patch(
            "app.services.rag.get_embedding_provider", return_value=embedding_provider
        ):
            results = rag._search_chunks("Q", [1, 2, 3], top_k=5)

        assert [r["document_id"] for r in results] == [1, 3]
        assert all(r["distance"] >= RETRIEVAL_SCORE_THRESHOLD for r in results)

    def test_keeps_hits_with_none_distance(self):
        """distance 缺失的命中保留（无法判断相关性时保守通过）。"""
        hits = [
            {"document_id": 1, "chunk_index": 0, "content": "x", "distance": None},
        ]
        rag, embedding_provider, _ = self._build_service(vector_hits=hits)

        with patch(
            "app.services.rag.get_embedding_provider", return_value=embedding_provider
        ):
            results = rag._search_chunks("Q", [], top_k=5)

        assert len(results) == 1

    def test_embedding_failure_returns_empty_for_external_fallback(self):
        """embedding 抛异常时 ``_search_chunks`` 返回 ``[]``（回退 external）。"""
        rag, _, vector_store = self._build_service()

        with patch(
            "app.services.rag.get_embedding_provider",
            side_effect=RuntimeError("model down"),
        ):
            results = rag._search_chunks("Q", [1], top_k=5)

        assert results == []
        vector_store.search.assert_not_called()

    def test_vector_store_failure_returns_empty(self):
        """vector_store.search 抛异常时返回 ``[]``。"""
        rag, embedding_provider, _ = self._build_service(
            search_raises=RuntimeError("milvus down")
        )

        with patch(
            "app.services.rag.get_embedding_provider", return_value=embedding_provider
        ):
            results = rag._search_chunks("Q", [1], top_k=5)

        assert results == []

    def test_empty_embedding_result_returns_empty(self):
        """embedding 返回空列表（极少见）时返回 ``[]``。"""
        rag, embedding_provider, vector_store = self._build_service()
        embedding_provider.embed_texts = MagicMock(return_value=[])

        with patch(
            "app.services.rag.get_embedding_provider", return_value=embedding_provider
        ):
            results = rag._search_chunks("Q", [1], top_k=5)

        assert results == []
        vector_store.search.assert_not_called()


class TestDedupeSources:
    """``RAGService._dedupe_sources`` 行为。"""

    def test_dedupes_by_document_id_preserving_order(self):
        hits = [
            _make_hit(1, 0, "a", 0.1),
            _make_hit(2, 0, "b", 0.2),
            _make_hit(1, 1, "a2", 0.3),  # 重复 document_id=1
            _make_hit(3, 0, "c", 0.4),
            _make_hit(2, 1, "b2", 0.5),  # 重复 document_id=2
        ]
        sources = RAGService._dedupe_sources(hits)
        assert sources == ["doc_1", "doc_2", "doc_3"]

    def test_skips_hits_with_none_document_id(self):
        hits = [
            _make_hit(1, 0, "a", 0.1),
            {"document_id": None, "chunk_index": 0, "content": "x", "distance": 0.2},
            _make_hit(2, 0, "b", 0.3),
        ]
        sources = RAGService._dedupe_sources(hits)
        assert sources == ["doc_1", "doc_2"]

    def test_empty_input_returns_empty(self):
        assert RAGService._dedupe_sources([]) == []
        assert RAGService._dedupe_sources(None) == []


class TestAretrieve:
    """``RAGService.aretrieve`` 把 history 透传给混合检索管线（#66）。"""

    @pytest.mark.asyncio
    async def test_passes_history_to_retrieve_evidence(self):
        rag = RAGService.__new__(RAGService)
        rag._vector_store = MagicMock()
        rag._request = None
        hit = MagicMock(document_id=1, chunk_index=0, content="c", score=0.5)
        pack = MagicMock()
        pack.hits = [hit]

        history = [{"role": "user", "content": "上一轮问题"}]
        with patch.object(
            rag, "retrieve_evidence", AsyncMock(return_value=pack)
        ) as mock_retrieve:
            results = await rag.aretrieve("Q", [1], top_k=5, history=history)

        mock_retrieve.assert_awaited_once_with("Q", [1], 5, history)
        assert results[0]["document_id"] == 1

    @pytest.mark.asyncio
    async def test_history_defaults_to_none(self):
        rag = RAGService.__new__(RAGService)
        rag._vector_store = MagicMock()
        rag._request = None
        pack = MagicMock()
        pack.hits = []

        with patch.object(
            rag, "retrieve_evidence", AsyncMock(return_value=pack)
        ) as mock_retrieve:
            await rag.aretrieve("Q", [1])

        mock_retrieve.assert_awaited_once_with("Q", [1], 5, None)


class TestRAGServiceAnswer:
    """``RAGService.answer`` 检索命中/未命中的 prompt 行为。"""

    @pytest.mark.asyncio
    async def test_uses_rag_prompt_when_search_hits(self):
        rag = RAGService.__new__(RAGService)
        rag._vector_store = MagicMock()
        rag._request = None

        hits = [_make_hit(7, 0, "needle content here", 0.1)]
        fake_llm = MagicMock()
        fake_llm.chat = AsyncMock(return_value="the answer")

        with patch("app.services.rag.get_embedding_provider") as mock_gs, patch.object(
            rag, "aretrieve", AsyncMock(return_value=hits)
        ), patch("app.services.rag.get_llm_provider", return_value=fake_llm):
            result = await rag.answer(
                question="What is X?",
                document_ids=[7],
                top_k=5,
            )

        # 命中 → RAG prompt，含 chunk content
        call_args = fake_llm.chat.call_args
        messages = call_args.args[0]
        prompt = messages[0]["content"]
        assert "needle content here" in prompt
        assert "[Document 1]" in prompt
        assert "What is X?" in prompt
        # sources 字段填充
        assert result["sources"] == ["doc_7"]
        assert result["used_external"] is False
        assert result["answer"] == "the answer"

    @pytest.mark.asyncio
    async def test_falls_back_to_external_prompt_when_no_hits(self):
        rag = RAGService.__new__(RAGService)
        rag._vector_store = MagicMock()
        rag._request = None

        fake_llm = MagicMock()
        fake_llm.chat = AsyncMock(return_value="general answer")

        with patch("app.services.rag.get_embedding_provider"), patch.object(
            rag, "aretrieve", AsyncMock(return_value=[])
        ), patch("app.services.rag.get_llm_provider", return_value=fake_llm):
            result = await rag.answer(
                question="Anything?",
                document_ids=[1, 2],
                top_k=5,
            )

        call_args = fake_llm.chat.call_args
        messages = call_args.args[0]
        prompt = messages[0]["content"]
        # 未命中 → external prompt，不含 [Document N]
        assert "[Document 1]" not in prompt
        assert "general knowledge" in prompt
        assert result["sources"] == []
        assert result["used_external"] is True

    @pytest.mark.asyncio
    async def test_dedupes_sources_across_multiple_hits(self):
        rag = RAGService.__new__(RAGService)
        rag._vector_store = MagicMock()
        rag._request = None

        hits = [
            _make_hit(1, 0, "a", 0.1),
            _make_hit(1, 1, "a2", 0.2),
            _make_hit(2, 0, "b", 0.3),
        ]
        fake_llm = MagicMock()
        fake_llm.chat = AsyncMock(return_value="ok")

        with patch("app.services.rag.get_embedding_provider"), patch.object(
            rag, "aretrieve", AsyncMock(return_value=hits)
        ), patch("app.services.rag.get_llm_provider", return_value=fake_llm):
            result = await rag.answer(question="Q", document_ids=[1, 2], top_k=5)

        # 按 document_id 去重
        assert result["sources"] == ["doc_1", "doc_2"]


class TestRAGServiceAnswerStream:
    """``RAGService.answer_stream`` 流式版本。"""

    @pytest.mark.asyncio
    async def test_yields_chunks_then_done_with_sources(self):
        rag = RAGService.__new__(RAGService)
        rag._vector_store = MagicMock()
        rag._request = None

        hits = [_make_hit(5, 0, "ctx", 0.1)]
        sources = ["doc_5"]

        async def fake_stream_chat(messages):
            for chunk in ["hello", " world"]:
                yield chunk

        fake_llm = MagicMock()
        fake_llm.stream_chat = fake_stream_chat

        with patch("app.services.rag.get_embedding_provider"), patch.object(
            rag, "aretrieve", AsyncMock(return_value=hits)
        ), patch("app.services.rag.get_llm_provider", return_value=fake_llm):
            events = []
            async for event in rag.answer_stream(
                question="Q", document_ids=[5], top_k=5
            ):
                events.append(event)

        # 第一个 done=False 事件 + 最后 done=True
        assert events[0]["chunk"] == "hello"
        assert events[0]["done"] is False
        assert events[0]["sources"] == sources
        assert events[1]["chunk"] == " world"
        assert events[1]["done"] is False
        # 最后 yield done=True
        final = events[-1]
        assert final["done"] is True
        assert final["sources"] == sources
        assert final["error"] is None

    @pytest.mark.asyncio
    async def test_yields_error_event_when_llm_raises(self):
        rag = RAGService.__new__(RAGService)
        rag._vector_store = MagicMock()
        rag._request = None

        async def fake_stream_chat(messages):
            raise RuntimeError("llm boom")
            yield  # unreachable — keeps this an async generator

        fake_llm = MagicMock()
        fake_llm.stream_chat = fake_stream_chat

        with patch("app.services.rag.get_embedding_provider"), patch.object(
            rag, "aretrieve", AsyncMock(return_value=[])
        ), patch("app.services.rag.get_llm_provider", return_value=fake_llm):
            events = []
            async for event in rag.answer_stream(
                question="Q", document_ids=[], top_k=5
            ):
                events.append(event)

        assert len(events) == 1
        assert events[0]["done"] is True
        assert "llm boom" in events[0]["error"]
        assert events[0]["sources"] == []  # 未命中 → 空 sources


class TestASearchChunksAsyncWrapper:
    """``_asearch_chunks`` 把同步阻塞调用丢到 executor，不阻塞事件循环。"""

    @pytest.mark.asyncio
    async def test_asearch_chunks_runs_in_executor(self):
        rag = RAGService.__new__(RAGService)
        rag._vector_store = MagicMock()
        rag._request = None

        # 同步 _search_chunks 替身
        def fake_sync_search(question, document_ids, top_k):
            return [_make_hit(1, 0, "x", 0.1)]

        rag._search_chunks = fake_sync_search

        result = await rag._asearch_chunks("Q", [1], top_k=5)
        assert len(result) == 1
        assert result[0]["document_id"] == 1

    @pytest.mark.asyncio
    async def test_asearch_chunks_propagates_result_from_sync(self):
        """``_asearch_chunks`` 直接返回 ``_search_chunks`` 的结果。"""
        rag = RAGService.__new__(RAGService)
        rag._vector_store = MagicMock()
        rag._request = None

        sentinel = [_make_hit(2, 0, "y", 0.2)]
        rag._search_chunks = MagicMock(return_value=sentinel)

        result = await rag._asearch_chunks("Q", [2], top_k=3)
        assert result is sentinel
        rag._search_chunks.assert_called_once_with("Q", [2], 3)


class TestRetrievalScoreThreshold:
    """``RETRIEVAL_SCORE_THRESHOLD`` 常量。"""

    def test_threshold_is_positive(self):
        assert RETRIEVAL_SCORE_THRESHOLD > 0
        assert RETRIEVAL_SCORE_THRESHOLD < 1  # COSINE 相似度在 [-1, 1] 范围，0.5 是合理截断
