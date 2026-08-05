"""Split streamed model output into thinking and answer segments.

Models such as MiniMax-M2 emit reasoning wrapped in <think>...</think> tags.
When streamed token-by-token, those tags may be split across chunks, so this
splitter buffers partial tag prefixes until enough context has arrived to
disambiguate. Callers feed chunks with `feed` and flush with `flush` once
the stream ends.
"""
from typing import List, Tuple

THINK_OPEN_TAG = "<think>"
THINK_CLOSE_TAG = "</think>"

Segment = Tuple[str, str]  # (kind, text); kind is "thinking" or "answer"


class ThinkSplitter:
    """Stateful splitter that separates <think>...</think> regions from the rest."""

    def __init__(self) -> None:
        self._buffer: str = ""
        self._in_thinking: bool = False

    def feed(self, chunk: str) -> List[Segment]:
        """Feed a chunk of text and return any complete segments."""
        if not chunk:
            return []
        self._buffer += chunk
        events: List[Segment] = []

        while True:
            if self._in_thinking:
                end_idx = self._buffer.find(THINK_CLOSE_TAG)
                if end_idx >= 0:
                    think_text = self._buffer[:end_idx]
                    if think_text:
                        events.append(("thinking", think_text))
                    self._buffer = self._buffer[end_idx + len(THINK_CLOSE_TAG):]
                    self._in_thinking = False
                    continue
                partial = self._partial_tag_suffix(self._buffer, THINK_CLOSE_TAG)
                if 0 < partial < len(THINK_CLOSE_TAG):
                    flushable = self._buffer[:-partial]
                    if flushable:
                        events.append(("thinking", flushable))
                    self._buffer = self._buffer[-partial:]
                else:
                    if self._buffer:
                        events.append(("thinking", self._buffer))
                        self._buffer = ""
                break
            else:
                start_idx = self._buffer.find(THINK_OPEN_TAG)
                if start_idx >= 0:
                    answer_text = self._buffer[:start_idx]
                    if answer_text:
                        events.append(("answer", answer_text))
                    self._buffer = self._buffer[start_idx + len(THINK_OPEN_TAG):]
                    self._in_thinking = True
                    continue
                partial = self._partial_tag_suffix(self._buffer, THINK_OPEN_TAG)
                if 0 < partial < len(THINK_OPEN_TAG):
                    flushable = self._buffer[:-partial]
                    if flushable:
                        events.append(("answer", flushable))
                    self._buffer = self._buffer[-partial:]
                else:
                    if self._buffer:
                        events.append(("answer", self._buffer))
                        self._buffer = ""
                break

        return events

    def flush(self) -> List[Segment]:
        """Flush any leftover buffer at end-of-stream."""
        if not self._buffer:
            return []
        kind = "thinking" if self._in_thinking else "answer"
        events: List[Segment] = [(kind, self._buffer)]
        self._buffer = ""
        self._in_thinking = False
        return events

    @staticmethod
    def _partial_tag_suffix(text: str, tag: str) -> int:
        """Length of the longest prefix of `tag` matching the suffix of `text`."""
        max_check = min(len(text), len(tag))
        for length in range(max_check, 0, -1):
            if text[-length:] == tag[:length]:
                return length
        return 0
