"""Unit tests for ThinkSplitter."""
from app.services.think_splitter import ThinkSplitter


def _concat(events, kind):
    """Concatenate text segments of the given kind."""
    return "".join(t for k, t in events if k == kind)


class TestThinkSplitterAnswerOnly:
    """Splitter leaves plain text untouched as 'answer'."""

    def test_single_chunk_pure_answer(self):
        splitter = ThinkSplitter()
        result = splitter.feed("Hello, world!")
        assert result == [("answer", "Hello, world!")]

    def test_multiple_chunks_pure_answer(self):
        splitter = ThinkSplitter()
        result = []
        result += splitter.feed("Hello")
        result += splitter.feed(", ")
        result += splitter.feed("world")
        result += splitter.feed("!")
        assert _concat(result, "answer") == "Hello, world!"
        assert _concat(result, "thinking") == ""


class TestThinkSplitterThinkingOnly:
    """A single full <think>...</think> block becomes a 'thinking' segment."""

    def test_single_full_block(self):
        splitter = ThinkSplitter()
        result = splitter.feed("<think>Let me think about this.</think>")
        assert _concat(result, "thinking") == "Let me think about this."
        assert _concat(result, "answer") == ""

    def test_multiple_full_blocks(self):
        splitter = ThinkSplitter()
        events = []
        events += splitter.feed("<think>First thought.</think>")
        events += splitter.feed("<think>Second thought.</think>")
        assert _concat(events, "thinking") == "First thought.Second thought."


class TestThinkSplitterMixed:
    """Answer + thinking + answer mix in any order."""

    def test_answer_then_thinking_then_answer(self):
        splitter = ThinkSplitter()
        events = []
        events += splitter.feed("Before ")
        events += splitter.feed("<think>thought</think>")
        events += splitter.feed(" after")
        events += splitter.flush()
        assert _concat(events, "answer") == "Before  after"
        assert _concat(events, "thinking") == "thought"


class TestThinkSplitterPartialTags:
    """Tags split across chunks must not leak to the output until complete."""

    def test_open_tag_split_across_chunks(self):
        splitter = ThinkSplitter()
        events = []
        events += splitter.feed("Answer<")
        events += splitter.feed("thi")
        events += splitter.feed("nk>Hello")
        events += splitter.flush()
        assert _concat(events, "answer") == "Answer"
        assert _concat(events, "thinking") == "Hello"

    def test_close_tag_split_across_chunks(self):
        splitter = ThinkSplitter()
        events = []
        events += splitter.feed("Before ")
        events += splitter.feed("<think>")
        events += splitter.feed("think")
        events += splitter.feed("</think")
        events += splitter.feed(">after")
        events += splitter.flush()
        assert _concat(events, "answer") == "Before after"
        assert _concat(events, "thinking") == "think"

    def test_partial_close_held_back_then_emitted(self):
        splitter = ThinkSplitter()
        events = []
        events += splitter.feed("<think>thinking")
        events += splitter.feed("</thin")
        events += splitter.feed("k>after")
        events += splitter.flush()
        assert _concat(events, "thinking") == "thinking"
        assert _concat(events, "answer") == "after"


class TestThinkSplitterFlush:
    """End-of-stream flush handles unfinished state."""

    def test_flush_empty(self):
        splitter = ThinkSplitter()
        assert splitter.flush() == []

    def test_flush_unclosed_think_block_emits_thinking(self):
        splitter = ThinkSplitter()
        events = []
        events += splitter.feed("<think>never closed")
        events += splitter.flush()
        assert _concat(events, "thinking") == "never closed"
        assert _concat(events, "answer") == ""

    def test_flush_partial_open_tag_emits_answer(self):
        splitter = ThinkSplitter()
        events = []
        events += splitter.feed("plain<thi")
        events += splitter.flush()
        assert _concat(events, "answer") == "plain<thi"
        assert _concat(events, "thinking") == ""

    def test_flush_after_clean_close_returns_remaining_answer(self):
        splitter = ThinkSplitter()
        events = []
        events += splitter.feed("<think>done</think>after")
        events += splitter.flush()
        assert _concat(events, "answer") == "after"
        assert _concat(events, "thinking") == "done"


class TestThinkSplitterNoTags:
    """Real-world text without any think tags must pass through cleanly."""

    def test_chinese_text(self):
        splitter = ThinkSplitter()
        events = splitter.feed("你好世界，这是一个普通回答。")
        assert events == [("answer", "你好世界，这是一个普通回答。")]

    def test_text_with_lt_symbol(self):
        splitter = ThinkSplitter()
        events = splitter.feed("Use 1 < 2 for comparison")
        assert events == [("answer", "Use 1 < 2 for comparison")]
