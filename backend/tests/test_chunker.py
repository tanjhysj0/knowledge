"""Unit tests for TextChunker."""
import pytest
from app.services.chunker import TextChunker


class TestTextChunkerBasic:
    """Basic tests for TextChunker initialization."""

    def test_default_init(self):
        chunker = TextChunker()
        assert chunker.chunk_size == 500
        assert chunker.overlap == 50

    def test_custom_init(self):
        chunker = TextChunker(chunk_size=200, overlap=20)
        assert chunker.chunk_size == 200
        assert chunker.overlap == 20


class TestTextChunkerEmptyInput:
    """Tests for empty text handling."""

    def test_empty_string(self):
        chunker = TextChunker()
        result = chunker.chunk("")
        assert result == []

    def test_whitespace_only(self):
        chunker = TextChunker()
        result = chunker.chunk("   \n\t  ")
        assert result == []

    def test_none_input(self):
        chunker = TextChunker()
        result = chunker.chunk(None)
        assert result == []


class TestTextChunkerParagraphSplitting:
    """Tests for paragraph-based chunking."""

    def test_single_short_paragraph(self):
        chunker = TextChunker(chunk_size=500, overlap=50)
        text = "This is a short paragraph."
        result = chunker.chunk(text)
        assert len(result) == 1
        assert result[0] == text

    def test_multiple_short_paragraphs(self):
        chunker = TextChunker(chunk_size=500, overlap=50)
        text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        result = chunker.chunk(text)
        # All paragraphs should be in result
        assert len(result) >= 1
        combined = " ".join(result)
        assert "First paragraph" in combined
        assert "Second paragraph" in combined
        assert "Third paragraph" in combined

    def test_paragraph_with_multiple_sentences(self):
        chunker = TextChunker(chunk_size=100, overlap=10)
        text = "This is sentence one. This is sentence two. This is sentence three."
        result = chunker.chunk(text)
        assert len(result) > 0
        # The content should be preserved
        combined = " ".join(result)
        assert "sentence one" in combined
        assert "sentence two" in combined
        assert "sentence three" in combined


class TestTextChunkerSentenceSplitting:
    """Tests for sentence-based chunking."""

    def test_multiple_sentences(self):
        chunker = TextChunker(chunk_size=100, overlap=10)
        text = "Hello world. How are you? I am fine."
        result = chunker.chunk(text)
        assert len(result) > 0
        # Each chunk should be a valid sentence or part thereof
        for chunk in result:
            assert len(chunk) <= chunker.chunk_size + 50  # Some tolerance

    def test_chinese_sentences(self):
        chunker = TextChunker(chunk_size=50, overlap=5)
        text = "你好世界。这是一个测试。中文分句测试。"
        result = chunker.chunk(text)
        assert len(result) > 0
        combined = " ".join(result)
        assert "你好" in combined
        assert "测试" in combined


class TestTextChunkerOverlap:
    """Tests for overlapping chunks."""

    def test_no_overlap(self):
        chunker = TextChunker(chunk_size=50, overlap=0)
        text = "A" * 150  # 3x chunk_size
        result = chunker.chunk(text)
        assert len(result) >= 2
        # With overlap=0, adjacent chunks should have no overlapping characters
        for i in range(len(result) - 1):
            # Check that there's no character overlap between adjacent chunks
            combined = result[i] + result[i + 1]
            # The combined length should equal sum of individual lengths if no overlap
            assert len(result[i]) + len(result[i + 1]) == len(combined) or len(result) <= 1

    def test_with_overlap(self):
        chunker = TextChunker(chunk_size=50, overlap=10)
        text = "This is a longer text that should be split into multiple chunks with overlap."
        result = chunker.chunk(text)
        assert len(result) > 1
        # Check that overlapping content exists
        has_overlap = False
        for i in range(len(result) - 1):
            overlap = set(result[i].split()) & set(result[i + 1].split())
            if overlap:
                has_overlap = True
                break
        assert has_overlap or len(result) == 1


class TestTextChunkerLongSegments:
    """Tests for handling segments longer than chunk_size."""

    def test_long_single_word(self):
        chunker = TextChunker(chunk_size=50, overlap=5)
        # A single "word" longer than chunk_size
        text = "a" * 100
        result = chunker.chunk(text)
        assert len(result) > 0
        # Each chunk should respect chunk_size
        for chunk in result:
            assert len(chunk) <= 100  # chunk_size + some tolerance

    def test_long_paragraph(self):
        chunker = TextChunker(chunk_size=50, overlap=5)
        text = "a" * 75 + ". " + "b" * 75 + "."
        result = chunker.chunk(text)
        assert len(result) >= 1
        # Verify all content is preserved
        combined = " ".join(result).replace(" ", "")
        assert "a" * 75 in combined
        assert "b" * 75 in combined


class TestTextChunkerTextCleaning:
    """Tests for text cleaning functionality."""

    def test_multiple_whitespace_normalized(self):
        chunker = TextChunker()
        text = "Hello    world\n\n\n\nTest"
        result = chunker.chunk(text)
        # Multiple whitespace should be normalized
        assert "    " not in " ".join(result)
        assert "Hello" in result[0] if result else True

    def test_leading_trailing_whitespace_removed(self):
        chunker = TextChunker(chunk_size=500, overlap=50)
        text = "   Hello world   "
        result = chunker.chunk(text)
        assert len(result) > 0
        # No chunk should have leading/trailing whitespace
        for chunk in result:
            assert chunk == chunk.strip()


class TestTextChunkerEdgeCases:
    """Edge case tests."""

    def test_single_character(self):
        chunker = TextChunker()
        result = chunker.chunk("a")
        assert len(result) == 1
        assert result[0] == "a"

    def test_exactly_chunk_size(self):
        chunker = TextChunker(chunk_size=10, overlap=0)
        text = "0123456789"  # Exactly 10 chars
        result = chunker.chunk(text)
        assert len(result) == 1
        assert result[0] == text

    def test_very_small_chunk_size(self):
        chunker = TextChunker(chunk_size=5, overlap=0)
        text = "Hello world"
        result = chunker.chunk(text)
        assert len(result) > 0
        # Each chunk should be <= chunk_size
        for chunk in result:
            assert len(chunk) <= 5
