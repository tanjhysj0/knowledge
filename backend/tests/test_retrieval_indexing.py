"""#66：索引构建抽取器单测（章节/实体/事件/BM25 纯函数 + LLM 事件抽取）。"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.retrieval.indexing import (
    _parse_event_result,
    build_bm25_chunks,
    build_chapter_anchors,
    extract_chapter_titles,
    extract_entity_anchors,
    extract_event_anchors,
    parse_chapter_number,
)


class TestParseChapterNumber:
    def test_arabic(self):
        assert parse_chapter_number("3") == 3
        assert parse_chapter_number("12") == 12

    def test_chinese_simple(self):
        assert parse_chapter_number("三") == 3
        assert parse_chapter_number("十") == 10
        assert parse_chapter_number("十二") == 12

    def test_chinese_compound(self):
        assert parse_chapter_number("二十三") == 23
        assert parse_chapter_number("一百零三") == 103


class TestExtractChapterTitles:
    def test_chinese_titles_in_order(self):
        text = "第一章 起源\n正文\n第二章 转折\n正文"
        assert extract_chapter_titles(text) == [(1, "起源"), (2, "转折")]

    def test_english_titles(self):
        text = "Chapter 1 The Beginning\nChapter 2 The End"
        assert extract_chapter_titles(text) == [(1, "The Beginning"), (2, "The End")]

    def test_dedupes_duplicate_numbers(self):
        text = "第一章 甲\n第一章 乙"
        assert extract_chapter_titles(text) == [(1, "甲")]

    def test_empty_title_uses_default(self):
        text = "第三章"
        assert extract_chapter_titles(text) == [(3, "第3章")]

    def test_no_titles(self):
        assert extract_chapter_titles("普通正文，没有章节") == []


class TestBuildChapterAnchors:
    def test_anchors_map_title_to_chunk_ranges(self):
        text = "第一章 起源\n第二章 转折"
        chunks = ["第一章 起源 内容", "第二章 转折 内容", "尾声"]
        anchors = build_chapter_anchors(1, text, chunks)

        assert [(a.chapter_no, a.chunk_start, a.chunk_end) for a in anchors] == [
            (1, 0, 1),
            (2, 1, 3),
        ]
        assert anchors[0].title == "起源"

    def test_no_titles_no_anchors(self):
        assert build_chapter_anchors(1, "正文", ["正文"]) == []


class TestExtractEntityAnchors:
    def test_keeps_frequent_entities_only(self):
        cuts = {
            "张三在这里": [("张三", "nr"), ("在", "p"), ("这里", "r")],
            "李四和张三见面": [("李四", "nr"), ("和", "c"), ("张三", "nr"), ("见面", "v")],
        }
        chunks = list(cuts.keys())
        with patch(
            "app.services.retrieval.indexing.pseg"
        ) as mock_pseg:
            mock_pseg.cut = lambda text: cuts[text]
            anchors = extract_entity_anchors(1, chunks)

        # 张三出现 2 次保留；李四只出现 1 次被过滤。
        names = {a.name for a in anchors}
        assert names == {"张三"}
        assert all(a.document_id == 1 for a in anchors)

    def test_short_words_ignored(self):
        cuts = {"张三": [("张三", "nr"), ("三", "nr")]}
        with patch("app.services.retrieval.indexing.pseg") as mock_pseg:
            mock_pseg.cut = lambda text: cuts[text]
            anchors = extract_entity_anchors(1, ["张三"])
        # 单字"三"长度不足被忽略，而"张三"只出现 1 次也被频次过滤。
        assert anchors == []


class TestParseEventResult:
    def _batch(self):
        return [(0, "内容零"), (1, "内容一")]

    def test_parses_events(self):
        raw = '{"events": [{"name": "大战", "description": "决战", "chunk": 1}]}'
        anchors = _parse_event_result(1, raw, self._batch())
        assert len(anchors) == 1
        assert anchors[0].name == "大战"
        assert anchors[0].chunk_index == 1
        assert anchors[0].content == "内容一"

    def test_tolerates_wrapped_json(self):
        raw = '好的：```json\n{"events": [{"name": "大战", "chunk": 0}]}\n```'
        anchors = _parse_event_result(1, raw, self._batch())
        assert len(anchors) == 1

    def test_invalid_payloads_return_empty(self):
        assert _parse_event_result(1, "", self._batch()) == []
        assert _parse_event_result(1, "not json", self._batch()) == []
        assert _parse_event_result(1, '{"events": "x"}', self._batch()) == []
        assert _parse_event_result(1, '{"events": [{"name": "x"}]}', self._batch()) == []


class TestBuildBm25Chunks:
    def test_builds_rows_with_tokens(self):
        rows = build_bm25_chunks(1, ["张三来了", "李四走了"])
        assert len(rows) == 2
        assert rows[0].document_id == 1
        assert rows[0].chunk_index == 0
        assert rows[0].content == "张三来了"
        assert rows[0].tokens  # 非空词序列（jieba 切分结果随词典版本变化）


class TestExtractEventAnchors:
    @pytest.mark.asyncio
    async def test_llm_not_configured_skips(self):
        # is_llm_configured 在函数内延迟 import，patch 目标在 app.services.llm。
        with patch("app.services.llm.is_llm_configured", return_value=(False, None)):
            assert await extract_event_anchors(1, ["c1"]) == []

    @pytest.mark.asyncio
    async def test_injected_llm_extracts_events(self):
        llm = MagicMock()
        llm.chat = AsyncMock(return_value='{"events": [{"name": "大战", "chunk": 0}]}')
        anchors = await extract_event_anchors(1, ["大战爆发"], llm=llm)
        assert len(anchors) == 1
        assert anchors[0].name == "大战"

    @pytest.mark.asyncio
    async def test_llm_failure_skips_batch(self):
        llm = MagicMock()
        llm.chat = AsyncMock(side_effect=RuntimeError("llm down"))
        assert await extract_event_anchors(1, ["c1"], llm=llm) == []
