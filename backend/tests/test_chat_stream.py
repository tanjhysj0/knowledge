"""Tests for /api/v1/chat/stream SSE behavior.

Verifies that the streaming endpoint emits clean SSE events with separate
'thinking' and 'message' kinds, and that the persisted ChatMessage content
never contains raw think tags.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.api.v1 import chat as chat_module
from app.api.v1.chat import chat_stream
from app.models.schemas import ChatRequest
from app.services.chat import stream_answer
from app.services.llm import reset_providers
from app.services.rag import RAGService


@pytest.fixture(autouse=True)
def reset_provider_instances():
    reset_providers()
    yield
    reset_providers()


@pytest.fixture(autouse=True)
def mock_rag_retrieve():
    """默认让 :meth:`RAGService.aretrieve` 返回 ``[]``。

    test_chat_stream 通过 ``/api/v1/chat/stream`` endpoint 调真 ``stream_answer``，
    不需要真加载 bge-m3（#32）。命中场景的测试可以显式 patch 覆盖。
    """
    with patch.object(
        RAGService, "aretrieve", new=AsyncMock(return_value=[]), create=True
    ):
        yield


@pytest.fixture(autouse=True)
def pass_llm_preflight(monkeypatch):
    """``#45`` 默认让 ``is_llm_configured`` 在 ``app.api.v1.chat`` 作用域内通过，
    避免 chat_stream 测试被 preflight 提前拒绝。需要验证 preflight 行为的测试
    必须在显式 patch 中覆盖本 fixture 的效果。
    """
    monkeypatch.setattr(chat_module, "is_llm_configured", lambda: (True, ""))


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

    def __init__(self, conversations=None, missing_conversation_ids=None):
        self.added: list = []
        self._conversations = conversations or []
        self._missing_conv_ids = set(missing_conversation_ids or [])

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = len(self.added)

    async def execute(self, statement):
        from sqlalchemy.sql import Select

        if isinstance(statement, Select):
            compiled = statement.compile()
            params = dict(compiled.params)
            sql = str(statement).lower()
            if "from conversations" in sql:
                conv_id = next(iter(params.values()), None)
                if conv_id is not None:
                    if conv_id in self._missing_conv_ids:
                        return _EmptyResult(value=None)
                    conv = next(
                        (c for c in self._conversations if c.id == conv_id),
                        None,
                    )
                    if conv is not None and not hasattr(conv, "updated_at"):
                        from datetime import datetime as _dt
                        conv.updated_at = _dt.utcnow()
                    return _EmptyResult(value=conv)
                return _EmptyResult(value=None)
        return _EmptyResult()

    async def commit(self):
        pass

    async def rollback(self):
        pass


class _EmptyResult:
    def __init__(self, *, value=None):
        self._value = value

    def scalars(self):
        return _EmptyScalars()

    def scalar_one_or_none(self):
        return self._value


class _EmptyScalars:
    def all(self):
        return []


class TestChatStreamSSE:
    """Verifies that /api/v1/chat/stream emits a clean SSE stream."""

    async def _drive(self, llm_chunks, document_ids=None, conversation_id=99):
        async def fake_stream(messages):
            for chunk in llm_chunks:
                yield chunk

        with patch("app.services.rag.RAGService._llm") as mock_llm:
            mock_llm.return_value.stream_chat = fake_stream
            payload = ChatRequest(
                message="Hi", document_ids=document_ids or [],
                conversation_id=conversation_id,
            )
            from types import SimpleNamespace
            db = _FakeSession(
                conversations=[SimpleNamespace(id=conversation_id, message_count=0)]
            )
            request = MagicMock(headers={})
            response = await chat_stream(request=request, payload=payload, db=db)
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


class TestStreamAnswerStrategies:
    """#75：``strategies`` 白名单经 stream_answer 透传给 aretrieve。"""

    @pytest.mark.asyncio
    async def test_forwards_strategies_to_aretrieve(self):
        async def fake_stream(messages):
            yield "ok"

        from types import SimpleNamespace

        db = _FakeSession(conversations=[SimpleNamespace(id=88, message_count=0)])
        with patch("app.services.rag.RAGService._llm") as mock_llm, patch.object(
            RAGService,
            "aretrieve",
            new=AsyncMock(return_value=[]),
            create=True,
        ) as mock_aretrieve:
            mock_llm.return_value.stream_chat = fake_stream
            events = await _collect_sse_dicts(
                stream_answer(
                    question="Q",
                    document_ids=[],
                    conversation_id=88,
                    db=db,
                    strategies=["dense"],
                )
            )

        assert any(e[0] == "done" for e in events)
        mock_aretrieve.assert_awaited_once_with(
            question="Q", document_ids=[], history=[], strategies=["dense"]
        )


class TestV1EndpointStrategyWhitelist:
    """#76：v1 端点接入层显式传入全量五路检索策略白名单。"""

    @pytest.mark.asyncio
    async def test_stream_endpoint_passes_full_whitelist(self):
        from types import SimpleNamespace

        async def fake_stream(**kwargs):
            yield {"event": "done", "data": json.dumps({"sources": []})}

        payload = ChatRequest(message="Hi", document_ids=[], conversation_id=99)
        request = MagicMock(headers={})
        db = _FakeSession(conversations=[SimpleNamespace(id=99, message_count=0)])

        with patch.object(
            chat_module.chat_service, "stream_answer", side_effect=fake_stream
        ) as mock_stream:
            response = await chat_module.chat_stream(
                request=request, payload=payload, db=db
            )
            await _collect_sse_dicts(response.body_iterator)

        mock_stream.assert_called_once()
        _, kwargs = mock_stream.call_args
        assert kwargs["strategies"] == ["dense", "bm25", "entity", "event", "chapter"]

    @pytest.mark.asyncio
    async def test_chat_endpoint_passes_full_whitelist(self):
        from types import SimpleNamespace

        payload = ChatRequest(message="Hi", document_ids=[], conversation_id=99)
        request = MagicMock(headers={})
        db = _FakeSession(conversations=[SimpleNamespace(id=99, message_count=0)])

        with patch.object(
            chat_module.chat_service,
            "ask",
            new=AsyncMock(return_value={"answer": "ok", "sources": []}),
        ) as mock_ask:
            await chat_module.chat(request=request, payload=payload, db=db)

        mock_ask.assert_called_once()
        _, kwargs = mock_ask.call_args
        assert kwargs["strategies"] == ["dense", "bm25", "entity", "event", "chapter"]


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
            payload = ChatRequest(message="Hello", document_ids=[], conversation_id=99)
            from types import SimpleNamespace
            db = _FakeSession(
                conversations=[SimpleNamespace(id=99, message_count=0)]
            )
            request = MagicMock(headers={})
            response = await chat_stream(request=request, payload=payload, db=db)
            async for _ in response.body_iterator:
                pass

        assistants = [obj for obj in db.added if getattr(obj, "role", None) == "assistant"]
        assert assistants, "expected assistant ChatMessage to be persisted"
        content = assistants[0].content
        assert "<think>" not in content
        assert "</think>" not in content
        assert content == "the answer"
