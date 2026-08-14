"""#66：BM25Retriever 单测（bm25_score 纯函数 + fake session 检索）。"""
import pytest

from app.models.retrieval_index import Bm25Chunk
from app.services.retrieval.bm25 import BM25Retriever, bm25_score, tokenize


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeSession:
    """记录语句、返回预置行的 AsyncSession 替身。"""

    def __init__(self, rows):
        self._rows = rows
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, statement):
        self.statements.append(statement)
        return _FakeResult(self._rows)


def _factory(rows):
    def make():
        return _FakeSession(rows)

    return make


def _chunk(doc_id, index, text, tokens):
    return Bm25Chunk(
        document_id=doc_id, chunk_index=index, content=text, tokens=tokens
    )


class TestTokenize:
    def test_splits_chinese_text(self):
        tokens = tokenize("张三打败了李四")
        assert "张三" in tokens
        assert "李四" in tokens

    def test_empty_text(self):
        assert tokenize("") == []
        assert tokenize("   ") == []


class TestBm25Score:
    def test_no_chunk_tokens_returns_zero(self):
        assert bm25_score(["a"], [], {"a": 1}, 10, 5.0) == 0.0

    def test_full_match_scores_positive(self):
        score = bm25_score(["张三"], ["张三", "大战", "张三"], {"张三": 2}, 10, 5.0)
        assert score > 0

    def test_no_overlap_returns_zero(self):
        assert bm25_score(["x"], ["张三", "大战"], {}, 10, 5.0) == 0.0

    def test_higher_term_frequency_scores_higher(self):
        """同文档内词频越高分数越高（长度归一化相同）。"""
        low = bm25_score(["张三"], ["张三"], {"张三": 2}, 10, 5.0)
        high = bm25_score(["张三"], ["张三", "张三"], {"张三": 2}, 10, 5.0)
        assert high > low

    def test_rarer_token_scores_higher(self):
        """df 越小 idf 越大：只命中 1 个 chunk 的词 > 命中所有 chunk 的词。"""
        rare = bm25_score(["张三"], ["张三"], {"张三": 1}, 10, 5.0)
        common = bm25_score(["张三"], ["张三"], {"张三": 10}, 10, 5.0)
        assert rare > common

    def test_longer_document_penalized(self):
        """长文档（相同词频）归一化后分数更低。"""
        short = bm25_score(["张三"], ["张三"], {"张三": 2}, 10, 5.0)
        long_doc = bm25_score(["张三"], ["张三"] + ["的"] * 9, {"张三": 2}, 10, 5.0)
        assert short > long_doc


class TestBM25Retriever:
    @pytest.mark.asyncio
    async def test_returns_top_k_sorted(self):
        chunks = [
            _chunk(1, 0, "张三大战李四", ["张三", "大战", "李四"]),
            _chunk(1, 1, "张三张三", ["张三", "张三"]),
            _chunk(1, 2, "无关内容", ["无关", "内容"]),
        ]
        retriever = BM25Retriever(session_factory=_factory(chunks))

        hits = await retriever.retrieve("张三", [1], top_k=2)

        assert [h.chunk_index for h in hits] == [1, 0]  # 词频高的排前
        assert all(h.strategy == "bm25" for h in hits)
        assert hits[0].content == "张三张三"

    @pytest.mark.asyncio
    async def test_zero_score_hits_excluded(self):
        chunks = [_chunk(1, 0, "无关内容", ["无关", "内容"])]
        retriever = BM25Retriever(session_factory=_factory(chunks))
        assert await retriever.retrieve("张三", [1]) == []

    @pytest.mark.asyncio
    async def test_document_ids_filter_applied(self):
        """document_ids 过滤条件进入查询语句。"""
        chunks = [_chunk(1, 0, "张三", ["张三"]), _chunk(2, 0, "张三", ["张三"])]
        session = _FakeSession(chunks)
        retriever = BM25Retriever(session_factory=lambda: session)

        hits = await retriever.retrieve("张三", [1], top_k=5)

        stmt = str(session.statements[0])
        assert "bm25_chunks" in stmt
        assert hits  # fake session 不做真实过滤，只验证语句已带条件
        assert "document_id" in stmt

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self):
        chunks = [_chunk(1, 0, "张三", ["张三"])]
        session = _FakeSession(chunks)
        retriever = BM25Retriever(session_factory=lambda: session)
        assert await retriever.retrieve("", [1]) == []
        assert session.statements == []  # 未查库

    @pytest.mark.asyncio
    async def test_db_failure_degrades_to_empty(self):
        def broken_factory():
            raise RuntimeError("db down")

        retriever = BM25Retriever(session_factory=broken_factory)
        assert await retriever.retrieve("张三", [1]) == []
