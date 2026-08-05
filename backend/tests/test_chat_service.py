"""Unit tests for the chat application service.

Uses ``_FakeAsyncSession`` to avoid touching a real DB and patches the LLM
streaming entrypoint (``RAGService._llm``) so we can drive the chat pipeline
deterministically. Mirrors the ``test_documents_service.py`` pattern from #38.
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import chat as chat_service
from app.services.chat import (
    ask,
    chat_history,
    clear_chat_history,
    stream_answer,
)
from app.services.llm import reset_providers


@pytest.fixture(autouse=True)
def reset_provider_instances():
    reset_providers()
    yield
    reset_providers()


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeExecuteResult:
    def __init__(self, *, rows=None, value=None):
        self._rows = rows or []
        self._value = value

    def scalar(self):
        return self._value

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeAsyncSession:
    """Minimal AsyncSession stand-in used by the chat service tests."""

    def __init__(self, *, history=None):
        self.added: list = []
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0
        self._history = history or []
        self._next_id = 1

    def add(self, obj):
        obj.id = self._next_id
        self._next_id += 1
        self.added.append(obj)

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def execute(self, statement):
        sql = str(statement).lower()
        if "from chat_messages" in sql:
            return _FakeExecuteResult(rows=self._history)
        return _FakeExecuteResult(rows=[])


async def _collect_sse_dicts(generator):
    """Decode the (event, data) dicts yielded by the chat service generator."""
    events = []
    async for ev in generator:
        try:
            events.append((ev["event"], json.loads(ev["data"])))
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return events


class TestAsk:
    """``ask`` delegates to RAG and persists user + assistant ChatMessages."""

    @pytest.mark.asyncio
    async def test_returns_answer_and_sources_from_rag(self):
        db = _FakeAsyncSession()
        rag_result = {"answer": "Hello back", "sources": ["doc_1"], "used_external": True}

        with patch.object(chat_service, "RAGService") as MockRAG:
            mock_instance = MockRAG.return_value
            mock_instance.answer = AsyncMock(return_value=rag_result)

            result = await ask(
                question="Hi",
                document_ids=[1],
                db=db,
            )

        assert result == {"answer": "Hello back", "sources": ["doc_1"]}
        mock_instance.answer.assert_called_once_with(
            question="Hi", document_ids=[1], top_k=5
        )

    @pytest.mark.asyncio
    async def test_persists_user_and_assistant_rows_with_doc_ids(self):
        db = _FakeAsyncSession()
        rag_result = {"answer": "Answer text", "sources": ["doc_2", "doc_3"], "used_external": False}

        with patch.object(chat_service, "RAGService") as MockRAG:
            MockRAG.return_value.answer = AsyncMock(return_value=rag_result)
            await ask(
                question="Question",
                document_ids=[2, 3],
                db=db,
            )

        users = [obj for obj in db.added if obj.role == "user"]
        assistants = [obj for obj in db.added if obj.role == "assistant"]

        assert len(users) == 1
        assert users[0].content == "Question"
        assert users[0].document_ids == "2,3"

        assert len(assistants) == 1
        assert assistants[0].content == "Answer text"
        assert assistants[0].document_ids == "doc_2,doc_3"

        assert db.commits == 1

    @pytest.mark.asyncio
    async def test_handles_empty_document_ids(self):
        db = _FakeAsyncSession()
        rag_result = {"answer": "x", "sources": [], "used_external": True}

        with patch.object(chat_service, "RAGService") as MockRAG:
            MockRAG.return_value.answer = AsyncMock(return_value=rag_result)
            await ask(
                question="Q",
                document_ids=[],
                db=db,
            )

        users = [obj for obj in db.added if obj.role == "user"]
        assistants = [obj for obj in db.added if obj.role == "assistant"]
        assert users[0].document_ids is None
        assert assistants[0].document_ids is None


class TestStreamAnswerHappyPath:
    """``stream_answer`` yields thinking/message/done events with persistence."""

    @pytest.mark.asyncio
    async def test_plain_chunks_yield_only_message_events(self):
        db = _FakeAsyncSession()

        async def fake_stream(messages):
            for chunk in ["Hello", " world", "!"]:
                yield chunk

        with patch("app.services.rag.RAGService._llm") as mock_llm:
            mock_llm.return_value.stream_chat = fake_stream
            events = await _collect_sse_dicts(
                stream_answer(question="Hi", document_ids=[], db=db)
            )

        message_events = [e for e in events if e[0] == "message"]
        thinking_events = [e for e in events if e[0] == "thinking"]
        assert message_events == [
            ("message", {"content": "Hello"}),
            ("message", {"content": " world"}),
            ("message", {"content": "!"}),
        ]
        assert thinking_events == []

    @pytest.mark.asyncio
    async def test_emits_done_event_with_sources(self):
        db = _FakeAsyncSession()

        async def fake_stream(messages):
            yield "Hi"

        with patch("app.services.rag.RAGService._llm") as mock_llm:
            mock_llm.return_value.stream_chat = fake_stream
            events = await _collect_sse_dicts(
                stream_answer(question="Hi", document_ids=[], db=db)
            )

        done_events = [e for e in events if e[0] == "done"]
        assert len(done_events) == 1
        assert done_events[0][1] == {"sources": []}

    @pytest.mark.asyncio
    async def test_thinking_and_answer_segments_split(self):
        db = _FakeAsyncSession()

        async def fake_stream(messages):
            yield "<think>reasoning</think>final"

        with patch("app.services.rag.RAGService._llm") as mock_llm:
            mock_llm.return_value.stream_chat = fake_stream
            events = await _collect_sse_dicts(
                stream_answer(question="Q", document_ids=[], db=db)
            )

        kinds = [e[0] for e in events]
        assert "thinking" in kinds
        assert "message" in kinds
        assert "done" in kinds
        thinking_contents = [e[1]["content"] for e in events if e[0] == "thinking"]
        message_contents = [e[1]["content"] for e in events if e[0] == "message"]
        assert "".join(thinking_contents) == "reasoning"
        assert "".join(message_contents) == "final"

    @pytest.mark.asyncio
    async def test_persists_assistant_message_after_success(self):
        db = _FakeAsyncSession()

        async def fake_stream(messages):
            yield "<think>reasoning</think>the answer"

        with patch("app.services.rag.RAGService._llm") as mock_llm:
            mock_llm.return_value.stream_chat = fake_stream
            await _collect_sse_dicts(
                stream_answer(question="Q", document_ids=[], db=db)
            )

        users = [obj for obj in db.added if obj.role == "user"]
        assistants = [obj for obj in db.added if obj.role == "assistant"]
        assert len(users) == 1
        assert len(assistants) == 1
        assert assistants[0].content == "the answer"
        assert "<think>" not in assistants[0].content
        assert db.commits == 1
        assert db.rollbacks == 0

    @pytest.mark.asyncio
    async def test_user_message_flushed_before_streaming(self):
        """User ChatMessage is flushed (id populated) before LLM streaming starts."""
        db = _FakeAsyncSession()

        async def fake_stream(messages):
            yield "ok"

        with patch("app.services.rag.RAGService._llm") as mock_llm:
            mock_llm.return_value.stream_chat = fake_stream
            await _collect_sse_dicts(
                stream_answer(question="Q", document_ids=[], db=db)
            )

        # Two flushes expected: one for user, one for assistant.
        assert db.flushes == 2
        users = [obj for obj in db.added if obj.role == "user"]
        assert users[0].id == 1

    @pytest.mark.asyncio
    async def test_passes_history_context_to_llm(self):
        """History rows (ordered by created_at asc) appear in messages sent to LLM."""
        history = [
            SimpleNamespace(role="user", content="earlier", document_ids=None, created_at="t1"),
            SimpleNamespace(role="assistant", content="earlier reply", document_ids=None, created_at="t2"),
        ]
        db = _FakeAsyncSession(history=history)
        captured_messages: list = []

        async def fake_stream(messages):
            captured_messages.extend(messages)
            yield "ok"

        with patch("app.services.rag.RAGService._llm") as mock_llm:
            mock_llm.return_value.stream_chat = fake_stream
            await _collect_sse_dicts(
                stream_answer(question="Q", document_ids=[], db=db)
            )

        # The user message and the constructed prompt are both appended.
        assert {"role": "user", "content": "earlier"} in captured_messages
        assert {"role": "assistant", "content": "earlier reply"} in captured_messages
        assert {"role": "user", "content": "Q"} in captured_messages
        # Last message is the constructed prompt ending in "general knowledge."
        assert captured_messages[-1]["role"] == "user"
        assert captured_messages[-1]["content"].startswith("Question: Q")
        assert captured_messages[-1]["content"].endswith("general knowledge.")


class TestStreamAnswerErrorPath:
    """``stream_answer`` falls back to a single ``error`` event after rollback."""

    @pytest.mark.asyncio
    async def test_llm_failure_yields_error_event_and_rolls_back(self):
        db = _FakeAsyncSession()

        async def fake_stream(messages):
            raise RuntimeError("boom")
            yield  # unreachable — keeps this an async generator function

        with patch("app.services.rag.RAGService._llm") as mock_llm:
            mock_llm.return_value.stream_chat = fake_stream
            events = await _collect_sse_dicts(
                stream_answer(question="Q", document_ids=[], db=db)
            )

        error_events = [e for e in events if e[0] == "error"]
        assert len(error_events) == 1
        assert "boom" in error_events[0][1]["error"]
        # No done event should follow an error.
        assert not [e for e in events if e[0] == "done"]
        # Rollback should have been called.
        assert db.rollbacks == 1
        # No commit on failure.
        assert db.commits == 0
        # Assistant row should not have been persisted.
        assistants = [obj for obj in db.added if obj.role == "assistant"]
        assert assistants == []

    @pytest.mark.asyncio
    async def test_llm_failure_during_loop_only_emits_error(self):
        db = _FakeAsyncSession()

        async def fake_stream(messages):
            yield "first "
            raise RuntimeError("midway")
            yield  # unreachable

        with patch("app.services.rag.RAGService._llm") as mock_llm:
            mock_llm.return_value.stream_chat = fake_stream
            events = await _collect_sse_dicts(
                stream_answer(question="Q", document_ids=[], db=db)
            )

        # Any pre-error events should still be observable; only error after.
        assert any(e[0] == "message" for e in events)
        error_events = [e for e in events if e[0] == "error"]
        assert len(error_events) == 1
        assert "midway" in error_events[0][1]["error"]
        assert db.rollbacks == 1


class TestChatHistory:
    """``chat_history`` returns rows from the DB in ascending order."""

    @pytest.mark.asyncio
    async def test_returns_rows(self):
        rows = [
            SimpleNamespace(id=1, role="user", content="hi", document_ids=None, created_at="t1"),
            SimpleNamespace(id=2, role="assistant", content="hello", document_ids=None, created_at="t2"),
        ]
        db = _FakeAsyncSession(history=rows)

        result = await chat_history(db)

        assert result == rows


class TestClearChatHistory:
    """``clear_chat_history`` issues a bulk delete and commits."""

    @pytest.mark.asyncio
    async def test_commits_after_delete(self):
        db = _FakeAsyncSession()

        await clear_chat_history(db)

        assert db.commits == 1
