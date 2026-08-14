"""#66：Reranker 单测（解析纯函数 + LLM 重排与直通降级）。"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.retrieval import RetrievalHit
from app.services.retrieval.reranker import LLMReranker, RERANK_MARKER, parse_rerank_result


def _hits() -> list:
    return [
        RetrievalHit(document_id=1, chunk_index=i, content=f"c{i}", score=0.9 - i * 0.1, strategy="dense")
        for i in range(3)
    ]


class TestParseRerankResult:
    def test_reorders_by_order_list(self):
        hits = _hits()
        reranked = parse_rerank_result('{"order": [2, 0, 1], "reject": []}', hits)
        assert [h.document_id + h.chunk_index for h in reranked] == [3, 1, 2]

    def test_rejects_noise(self):
        hits = _hits()
        reranked = parse_rerank_result('{"order": [2, 0], "reject": [1]}', hits)
        assert len(reranked) == 2

    def test_appends_unordered_hits(self):
        hits = _hits()
        reranked = parse_rerank_result('{"order": [1], "reject": []}', hits)
        assert len(reranked) == 3
        assert reranked[0].chunk_index == 1

    def test_invalid_json_passthrough(self):
        hits = _hits()
        assert parse_rerank_result("garbage", hits) == hits
        assert parse_rerank_result("", hits) == hits
        assert parse_rerank_result('{"other": 1}', hits) == hits

    def test_out_of_range_indices_ignored(self):
        hits = _hits()
        reranked = parse_rerank_result('{"order": [99, 0], "reject": []}', hits)
        assert [h.chunk_index for h in reranked] == [0, 1, 2]


class TestLLMReranker:
    @pytest.mark.asyncio
    async def test_rerank_calls_llm_with_marker(self):
        llm = MagicMock()
        llm.chat = AsyncMock(return_value='{"order": [1, 0]}')
        reranker = LLMReranker(llm=llm)
        hits = _hits()[:2]

        result = await reranker.rerank("Q", hits)

        assert [h.chunk_index for h in result] == [1, 0]
        prompt = llm.chat.call_args.kwargs["messages"][0]["content"]
        assert prompt.startswith(RERANK_MARKER)
        assert "Q" in prompt

    @pytest.mark.asyncio
    async def test_single_hit_skips_llm(self):
        llm = MagicMock()
        reranker = LLMReranker(llm=llm)
        hits = _hits()[:1]
        result = await reranker.rerank("Q", hits)
        assert result == hits
        llm.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_failure_passthrough(self):
        llm = MagicMock()
        llm.chat = AsyncMock(side_effect=RuntimeError("llm down"))
        reranker = LLMReranker(llm=llm)
        hits = _hits()
        result = await reranker.rerank("Q", hits)
        assert result == hits  # 直通不过滤

    @pytest.mark.asyncio
    async def test_garbage_output_passthrough(self):
        llm = MagicMock()
        llm.chat = AsyncMock(return_value="not a valid result")
        reranker = LLMReranker(llm=llm)
        hits = _hits()
        assert await reranker.rerank("Q", hits) == hits
