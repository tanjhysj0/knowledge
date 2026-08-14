"""#66：Chapter/Entity/Event 三路元数据检索器单测（fake session）。"""
import pytest

from app.models.retrieval_index import Bm25Chunk, ChapterAnchor, EntityAnchor, EventAnchor
from app.services.retrieval.metadata import (
    ChapterRetriever,
    EntityRetriever,
    EventRetriever,
    extract_chapter_no,
)


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
    """按过滤函数模拟 SQL WHERE 的 AsyncSession 替身（过滤推给"数据库"）。"""

    def __init__(self, rows, filter_fn=None):
        self._rows = rows
        self._filter_fn = filter_fn
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, statement):
        self.statements.append(statement)
        rows = [
            r for r in self._rows
            if self._filter_fn is None or self._filter_fn(statement, r)
        ]
        return _FakeResult(rows)


def _factory(rows, filter_fn=None):
    def make():
        return _FakeSession(rows, filter_fn)

    return make


class TestExtractChapterNo:
    def test_arabic_chinese(self):
        assert extract_chapter_no("第3章讲了什么") == 3
        assert extract_chapter_no("看看第三回") == 3

    def test_chinese_number(self):
        assert extract_chapter_no("第十二章内容") == 12
        assert extract_chapter_no("第一百零三节") == 103

    def test_english_chapter(self):
        assert extract_chapter_no("Chapter 5 summary") == 5

    def test_no_chapter_returns_none(self):
        assert extract_chapter_no("主角是谁") is None


class TestChapterRetriever:
    @pytest.mark.asyncio
    async def test_returns_chunks_in_chapter_range(self):
        anchor = ChapterAnchor(
            document_id=1, chapter_no=3, title="第三话", chunk_start=2, chunk_end=4
        )
        chunks = [
            Bm25Chunk(document_id=1, chunk_index=2, content="c2", tokens=[]),
            Bm25Chunk(document_id=1, chunk_index=3, content="c3", tokens=[]),
            Bm25Chunk(document_id=1, chunk_index=4, content="c4", tokens=[]),  # 区间外
        ]

        def _filter(statement, row):
            # 模拟 SQL：按语句路由，anchor 按章节号、chunk 按 [start, end) 区间过滤。
            sql = str(statement).lower()
            if "bm25_chunks" in sql:
                return isinstance(row, Bm25Chunk) and anchor.chunk_start <= row.chunk_index < anchor.chunk_end
            if "chapter_anchors" in sql:
                return isinstance(row, ChapterAnchor) and row.chapter_no == 3
            return False

        retriever = ChapterRetriever(session_factory=_factory([anchor, *chunks], _filter))

        hits = await retriever.retrieve("第3章讲了什么", [1], top_k=5)

        assert [h.chunk_index for h in hits] == [2, 3]
        assert all(h.strategy == "chapter" for h in hits)
        assert all(h.chapter == "第三话" for h in hits)

    @pytest.mark.asyncio
    async def test_no_chapter_number_returns_empty(self):
        session = _FakeSession([])
        retriever = ChapterRetriever(session_factory=lambda: session)
        assert await retriever.retrieve("主角是谁", [1]) == []
        assert session.statements == []  # 未查库

    @pytest.mark.asyncio
    async def test_db_failure_degrades_to_empty(self):
        retriever = ChapterRetriever(session_factory=lambda: (_ for _ in ()).throw(RuntimeError("db down")))
        assert await retriever.retrieve("第3章", [1]) == []


class TestEntityRetriever:
    @pytest.mark.asyncio
    async def test_matches_query_tokens(self):
        anchor = EntityAnchor(
            document_id=1, name="张三", kind="nr", chunk_index=1, content="张三来了"
        )
        retriever = EntityRetriever(session_factory=_factory([anchor]))

        hits = await retriever.retrieve("张三是谁", [1])

        assert len(hits) == 1
        assert hits[0].strategy == "entity"
        assert hits[0].chunk_index == 1

    @pytest.mark.asyncio
    async def test_no_overlap_returns_empty(self):
        """查询词不在锚点名中 → SQL in_ 过滤后无结果 → 空列表。"""
        anchor = EntityAnchor(
            document_id=1, name="王五", kind="nr", chunk_index=1, content="王五来了"
        )
        retriever = EntityRetriever(session_factory=_factory([anchor], lambda s, r: False))
        assert await retriever.retrieve("李四是谁", [1]) == []

    @pytest.mark.asyncio
    async def test_db_failure_degrades_to_empty(self):
        retriever = EntityRetriever(session_factory=lambda: (_ for _ in ()).throw(RuntimeError("db down")))
        assert await retriever.retrieve("张三", [1]) == []


class TestEventRetriever:
    @pytest.mark.asyncio
    async def test_matches_event_name_overlap(self):
        anchor = EventAnchor(
            document_id=1, name="大战", description="终局之战", chunk_index=0, content="大战爆发"
        )
        retriever = EventRetriever(session_factory=_factory([anchor]))

        hits = await retriever.retrieve("大战结果如何", [1])

        assert len(hits) == 1
        assert hits[0].strategy == "event"

    @pytest.mark.asyncio
    async def test_document_ids_filter(self):
        anchor = EventAnchor(
            document_id=2, name="大战", description="", chunk_index=0, content="大战"
        )
        retriever = EventRetriever(session_factory=_factory([anchor]))

        assert await retriever.retrieve("大战", [1]) == []
        assert await retriever.retrieve("大战", [2]) != []

    @pytest.mark.asyncio
    async def test_no_overlap_returns_empty(self):
        anchor = EventAnchor(
            document_id=1, name="重逢", description="", chunk_index=0, content="重逢"
        )
        retriever = EventRetriever(session_factory=_factory([anchor]))
        assert await retriever.retrieve("大战结果", [1]) == []

    @pytest.mark.asyncio
    async def test_db_failure_degrades_to_empty(self):
        retriever = EventRetriever(session_factory=lambda: (_ for _ in ()).throw(RuntimeError("db down")))
        assert await retriever.retrieve("大战", [1]) == []
