"""Tests for /api/chat/stream SSE behavior.

Verifies that the streaming endpoint emits clean SSE events with separate
'thinking' and 'message' kinds, and that the persisted ChatMessage content
never contains raw think tags.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.api.chat import chat_stream
from app.models.schemas import ChatRequest
from app.services.llm import reset_providers


@pytest.fixture(autouse=True)
def reset_provider_instances():
    reset_providers()
    yield
    reset_providers()


async def _collect_sse_dicts(generator):
    """Decode the (event, data) dicts yielded by chat_stream's event_generator."""
    events = []
    async for ev in generator:
        try:
            events.append((ev["event"], json.loads(ev["data"])))
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return events


class _FakeSession:
    """Minimal AsyncSession stand-in used as the chat_stream db dependency."""

    def __init__(self):
        self.added: list = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = len(self.added)

    async def execute(self, *_args, **_kwargs):
        return _EmptyResult()

    async def commit(self):
        pass

    async def rollback(self):
        pass


class _EmptyResult:
    def scalars(self):
        return _EmptyScalars()


class _EmptyScalars:
    def all(self):
        return []


class TestChatStreamSSE:
    """Verifies that /api/chat/stream emits a clean SSE stream."""

    async def _drive(self, llm_chunks, document_ids=None):
        async def fake_stream(messages):
            for chunk in llm_chunks:
                yield chunk

        with patch("app.services.rag.RAGService._llm") as mock_llm:
            mock_llm.return_value.stream_chat = fake_stream
            request = ChatRequest(message="Hi", document_ids=document_ids or [])
            db = _FakeSession()
            response = await chat_stream(request=request, db=db)
            # EventSourceResponse is an object that exposes .body_iterator.
            return await _collect_sse_dicts(response.body_iterator)

    @pytest.mark.asyncio
    async def test_stream_emits_only_message_events_for_plain_answer(self):
        events = await self._drive(["Hello", " world", "!"])
        message_events = [e for e in events if e[0] == "message"]
        thinking_events = [e for e in events if e[0] == "thinking"]
        assert message_events == [
            ("message", {"content": "Hello"}),
            ("message", {"content": " world"}),
            ("message", {"content": "!"}),
        ]
        assert thinking_events == []

    @pytest.mark.asyncio
    async def test_stream_emits_thinking_events_for_think_blocks(self):
        events = await self._drive(["<think>reasoning</think>final"])
        kinds = [e[0] for e in events]
        assert "thinking" in kinds
        assert "message" in kinds

        thinking_contents = [e[1]["content"] for e in events if e[0] == "thinking"]
        message_contents = [e[1]["content"] for e in events if e[0] == "message"]
        assert "".join(thinking_contents) == "reasoning"
        assert "".join(message_contents) == "final"

    @pytest.mark.asyncio
    async def test_stream_split_thinking_across_chunks(self):
        events = await self._drive(["<think>par", "tial</think>ans"])
        thinking_contents = [e[1]["content"] for e in events if e[0] == "thinking"]
        message_contents = [e[1]["content"] for e in events if e[0] == "message"]
        assert "".join(thinking_contents) == "partial"
        assert "".join(message_contents) == "ans"

    @pytest.mark.asyncio
    async def test_stream_emits_done_event(self):
        events = await self._drive(["Hi"])
        done_events = [e for e in events if e[0] == "done"]
        assert len(done_events) == 1

    @pytest.mark.asyncio
    async def test_done_event_has_sources(self):
        events = await self._drive(["Hi"])
        done = next((e for e in events if e[0] == "done"), None)
        assert done is not None
        assert "sources" in done[1]
        assert done[1]["sources"] == []


class TestChatStreamPersistedContent:
    """The persisted assistant ChatMessage.content must contain no think tags."""

    @pytest.mark.asyncio
    async def test_persisted_content_excludes_thinking(self):
        async def fake_stream(messages):
            for chunk in [
                "<think>internal reasoning</think>",
                "the answer",
            ]:
                yield chunk

        with patch("app.services.rag.RAGService._llm") as mock_llm:
            mock_llm.return_value.stream_chat = fake_stream
            request = ChatRequest(message="Hello", document_ids=[])
            db = _FakeSession()
            response = await chat_stream(request=request, db=db)
            async for _ in response.body_iterator:
                pass

        assistants = [obj for obj in db.added if getattr(obj, "role", None) == "assistant"]
        assert assistants, "expected assistant ChatMessage to be persisted"
        content = assistants[0].content
        assert "<think>" not in content
        assert "</think>" not in content
        assert content == "the answer"
