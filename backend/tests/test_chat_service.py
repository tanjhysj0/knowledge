"""Unit tests for the chat application service.

Uses ``_FakeAsyncSession`` to avoid touching a real DB and patches the LLM
streaming entrypoint (``RAGService._llm``) so we can drive the chat pipeline
deterministically. Mirrors the ``test_documents_service.py`` pattern from #38.

#36：``conversation_id`` 必填；伪造会话可通过 ``_FakeAsyncSession(conversations=[...])``
注入；``chat_history`` 废除（使用 :func:`app.services.conversations.list_messages`）。
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import chat as chat_service
from app.services.chat import ask, stream_answer
from app.services.conversations import ConversationNotFoundError
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

    测试不需要真加载 bge-m3（#32）。需要在命中场景下造数据的测试
    （如 :class:`TestAsk`）会显式 mock ``aretrieve`` 覆盖本 fixture。
    """
    with patch.object(
        RAGService, "aretrieve", new=AsyncMock(return_value=[]), create=True
    ):
        yield


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

    def scalar_one_or_none(self):
        return self._value


class _FakeAsyncSession:
    """Minimal AsyncSession stand-in used by the chat service tests.

    #36：支持会话查找（``get_conversation``）与按 ``conversation_id`` 过滤的
    ``chat_messages`` 查询，使隔离测试不需要真数据库。

    #63：``ready_document_ids`` 模拟 ``documents`` 表查询，仅返回 ready 状态
    的小说 id（对应 :func:`app.services.chat._filter_ready_document_ids`）。
    """

    def __init__(self, *, history=None, conversations=None,
                 missing_conversation_ids=None, ready_document_ids=None):
        self.added: list = []
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0
        self._history = history or []
        self._conversations = conversations or []
        self._missing_conv_ids = set(missing_conversation_ids or [])
        self._ready_document_ids = ready_document_ids or []
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
        from sqlalchemy.sql import Select

        if isinstance(statement, Select):
            # #36：从 compile() 拿参数值，比正则字面量靠讲
            compiled = statement.compile()
            params = dict(compiled.params)
            sql = str(statement).lower()
            if "from chat_messages" in sql:
                target = self._history
                # 取 conversation_id 参数（如果 WHERE 里有）
                cid = None
                for key, val in params.items():
                    if "conversation_id" in key.lower():
                        cid = val
                        break
                if cid is not None:
                    target = [
                        row for row in self._history
                        if getattr(row, "conversation_id", None) == cid
                    ]
                return _FakeExecuteResult(rows=target)
            if "from conversations" in sql:
                # 取 id 参数
                conv_id = None
                for key, val in params.items():
                    if key.lower().endswith("id_1") or "conversations.id" in str(statement).lower():
                        conv_id = val
                        break
                if conv_id is not None:
                    if conv_id in self._missing_conv_ids:
                        return _FakeExecuteResult(value=None)
                    conv = next(
                        (c for c in self._conversations if c.id == conv_id),
                        None,
                    )
                    if conv is not None and not hasattr(conv, "updated_at"):
                        # touch_conversation 需要 ``updated_at`` 字段写入以触发 onupdate
                        from datetime import datetime as _dt
                        conv.updated_at = _dt.utcnow()
                    return _FakeExecuteResult(value=conv)
                return _FakeExecuteResult(value=None)
            # #63：documents 表查询——仅返回 ready 状态的小说 id（标量）
            if "from documents" in sql:
                return _FakeExecuteResult(rows=list(self._ready_document_ids))
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
        db = _FakeAsyncSession(
            conversations=[SimpleNamespace(id=10, message_count=0)],
            ready_document_ids=[1],  # #63：id=1 已 ready，参与检索
        )
        # chat.py:ask 现在显式调 RAGService.aretrieve + RAGService._llm().chat，
        # 不再调 RAGService.answer（#32 + #33 重构）。
        fake_hits = [{"document_id": 1, "chunk_index": 0, "content": "ctx", "distance": 0.1}]
        fake_llm = MagicMock()
        fake_llm.chat = AsyncMock(return_value="Hello back")

        with patch.object(chat_service, "RAGService") as MockRAG:
            mock_instance = MockRAG.return_value
            mock_instance.aretrieve = AsyncMock(return_value=fake_hits)
            mock_instance._llm = MagicMock(return_value=fake_llm)
            mock_instance._build_rag_prompt = MagicMock(return_value="RAG PROMPT")
            mock_instance._build_external_prompt = MagicMock(return_value="EXT PROMPT")
            mock_instance._dedupe_sources = RAGService._dedupe_sources  # 用真实实现

            result = await ask(
                question="Hi",
                document_ids=[1],
                conversation_id=10,
                db=db,
            )

        assert result == {"answer": "Hello back", "sources": ["doc_1"]}
        mock_instance.aretrieve.assert_called_once_with(
            question="Hi", document_ids=[1], top_k=5
        )
        # 命中时使用 RAG prompt
        mock_instance._build_rag_prompt.assert_called_once()
        mock_instance._build_external_prompt.assert_not_called()

    @pytest.mark.asyncio
    async def test_persists_user_and_assistant_rows_with_doc_ids(self):
        db = _FakeAsyncSession(
            conversations=[SimpleNamespace(id=11, message_count=0)],
            ready_document_ids=[2, 3],  # #63：两个小说均已 ready
        )
        fake_hits = [
            {"document_id": 2, "chunk_index": 0, "content": "x", "distance": 0.1},
            {"document_id": 3, "chunk_index": 0, "content": "y", "distance": 0.2},
        ]
        fake_llm = MagicMock()
        fake_llm.chat = AsyncMock(return_value="Answer text")

        with patch.object(chat_service, "RAGService") as MockRAG:
            mock_instance = MockRAG.return_value
            mock_instance.aretrieve = AsyncMock(return_value=fake_hits)
            mock_instance._llm = MagicMock(return_value=fake_llm)
            mock_instance._build_rag_prompt = MagicMock(return_value="RAG PROMPT")
            mock_instance._build_external_prompt = MagicMock(return_value="EXT PROMPT")
            mock_instance._dedupe_sources = RAGService._dedupe_sources

            await ask(
                question="Question",
                document_ids=[2, 3],
                conversation_id=11,
                db=db,
            )

        users = [obj for obj in db.added if obj.role == "user"]
        assistants = [obj for obj in db.added if obj.role == "assistant"]

        assert len(users) == 1
        assert users[0].content == "Question"
        assert users[0].document_ids == "2,3"
        assert users[0].conversation_id == 11

        assert len(assistants) == 1
        assert assistants[0].content == "Answer text"
        assert assistants[0].document_ids == "doc_2,doc_3"
        assert assistants[0].conversation_id == 11

        # #36：写完消息 + touch_conversation 各 commit 一次
        assert db.commits == 2

    @pytest.mark.asyncio
    async def test_handles_empty_document_ids(self):
        db = _FakeAsyncSession(conversations=[SimpleNamespace(id=12, message_count=0)])
        # 空 doc_ids 时 aretrieve 返回 []，回退 external prompt
        fake_llm = MagicMock()
        fake_llm.chat = AsyncMock(return_value="x")

        with patch.object(chat_service, "RAGService") as MockRAG:
            mock_instance = MockRAG.return_value
            mock_instance.aretrieve = AsyncMock(return_value=[])
            mock_instance._llm = MagicMock(return_value=fake_llm)
            mock_instance._build_rag_prompt = MagicMock(return_value="RAG PROMPT")
            mock_instance._build_external_prompt = MagicMock(return_value="EXT PROMPT")
            mock_instance._dedupe_sources = RAGService._dedupe_sources

            await ask(
                question="Q",
                document_ids=[],
                conversation_id=12,
                db=db,
            )

        users = [obj for obj in db.added if obj.role == "user"]
        assistants = [obj for obj in db.added if obj.role == "assistant"]
        assert users[0].document_ids is None
        assert assistants[0].document_ids is None
        # 未命中 → external prompt
        mock_instance._build_external_prompt.assert_called_once()
        mock_instance._build_rag_prompt.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_conversation_raises_not_found(self):
        """#36：不存在的 ``conversation_id`` 应抛 :class:`ConversationNotFoundError`，
        不写入任何消息、不调 LLM。"""
        db = _FakeAsyncSession(missing_conversation_ids=[999])
        fake_llm = MagicMock()
        fake_llm.chat = AsyncMock(return_value="x")

        with patch.object(chat_service, "RAGService") as MockRAG:
            mock_instance = MockRAG.return_value
            mock_instance.aretrieve = AsyncMock(return_value=[])
            mock_instance._llm = MagicMock(return_value=fake_llm)
            mock_instance._dedupe_sources = RAGService._dedupe_sources

            with pytest.raises(ConversationNotFoundError):
                await ask(
                    question="orphan",
                    document_ids=[],
                    conversation_id=999,
                    db=db,
                )

        assert db.added == []
        assert db.commits == 0
        mock_instance._llm.assert_not_called()


class TestReadyDocumentFilter:
    """#63：未 ``ready`` 的小说不参与 RAG 检索，但落库消息仍存原始 ids。"""

    @pytest.mark.asyncio
    async def test_ask_filters_non_ready_documents_before_retrieve(self):
        db = _FakeAsyncSession(
            conversations=[SimpleNamespace(id=60, message_count=0)],
            ready_document_ids=[2],
        )
        fake_llm = MagicMock()
        fake_llm.chat = AsyncMock(return_value="ok")

        with patch.object(chat_service, "RAGService") as MockRAG:
            mock_instance = MockRAG.return_value
            mock_instance.aretrieve = AsyncMock(return_value=[
                {"document_id": 2, "chunk_index": 0, "content": "x", "distance": 0.1}
            ])
            mock_instance._llm = MagicMock(return_value=fake_llm)
            mock_instance._build_rag_prompt = MagicMock(return_value="RAG")
            mock_instance._build_external_prompt = MagicMock(return_value="EXT")
            mock_instance._dedupe_sources = RAGService._dedupe_sources

            await ask(
                question="Q",
                document_ids=[1, 2, 3],
                conversation_id=60,
                db=db,
            )

        # 仅 ready 的 id=2 参与检索
        mock_instance.aretrieve.assert_called_once_with(
            question="Q", document_ids=[2], top_k=5
        )
        # 落库的 user 消息仍存原始 ids
        users = [obj for obj in db.added if obj.role == "user"]
        assert users[0].document_ids == "1,2,3"

    @pytest.mark.asyncio
    async def test_ask_all_non_ready_falls_back_to_external_prompt(self):
        db = _FakeAsyncSession(
            conversations=[SimpleNamespace(id=61, message_count=0)],
            ready_document_ids=[],  # 全部未 ready
        )
        fake_llm = MagicMock()
        fake_llm.chat = AsyncMock(return_value="external answer")

        with patch.object(chat_service, "RAGService") as MockRAG:
            mock_instance = MockRAG.return_value
            mock_instance.aretrieve = AsyncMock(return_value=[])
            mock_instance._llm = MagicMock(return_value=fake_llm)
            mock_instance._build_rag_prompt = MagicMock(return_value="RAG")
            mock_instance._build_external_prompt = MagicMock(return_value="EXT")
            mock_instance._dedupe_sources = RAGService._dedupe_sources

            await ask(
                question="Q",
                document_ids=[7],
                conversation_id=61,
                db=db,
            )

        mock_instance.aretrieve.assert_called_once_with(
            question="Q", document_ids=[], top_k=5
        )
        mock_instance._build_external_prompt.assert_called_once()
        mock_instance._build_rag_prompt.assert_not_called()

    @pytest.mark.asyncio
    async def test_stream_answer_filters_non_ready_documents(self):
        db = _FakeAsyncSession(
            conversations=[SimpleNamespace(id=62, message_count=0)],
            ready_document_ids=[6],
        )

        async def fake_stream(messages):
            yield "ok"

        with patch.object(chat_service, "RAGService") as MockRAG, \
             patch("app.services.rag.RAGService._llm") as mock_llm:
            mock_instance = MockRAG.return_value
            mock_instance.aretrieve = AsyncMock(return_value=[])
            mock_instance._dedupe_sources = RAGService._dedupe_sources
            mock_instance._build_external_prompt = MagicMock(return_value="EXT")
            mock_llm.return_value.stream_chat = fake_stream

            await _collect_sse_dicts(
                stream_answer(question="Q", document_ids=[5, 6],
                              conversation_id=62, db=db)
            )

        mock_instance.aretrieve.assert_called_once_with(
            question="Q", document_ids=[6], top_k=5
        )


class TestStreamAnswerHappyPath:
    """``stream_answer`` yields thinking/message/done events with persistence."""

    @pytest.mark.asyncio
    async def test_plain_chunks_yield_only_message_events(self):
        db = _FakeAsyncSession(conversations=[SimpleNamespace(id=20, message_count=0)])

        async def fake_stream(messages):
            for chunk in ["Hello", " world", "!"]:
                yield chunk

        with patch("app.services.rag.RAGService._llm") as mock_llm:
            mock_llm.return_value.stream_chat = fake_stream
            events = await _collect_sse_dicts(
                stream_answer(question="Hi", document_ids=[], conversation_id=20, db=db)
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
        db = _FakeAsyncSession(conversations=[SimpleNamespace(id=21, message_count=0)])

        async def fake_stream(messages):
            yield "Hi"

        with patch("app.services.rag.RAGService._llm") as mock_llm:
            mock_llm.return_value.stream_chat = fake_stream
            events = await _collect_sse_dicts(
                stream_answer(question="Hi", document_ids=[], conversation_id=21, db=db)
            )

        done_events = [e for e in events if e[0] == "done"]
        assert len(done_events) == 1
        assert done_events[0][1] == {"sources": []}

    @pytest.mark.asyncio
    async def test_thinking_and_answer_segments_split(self):
        db = _FakeAsyncSession(conversations=[SimpleNamespace(id=22, message_count=0)])

        async def fake_stream(messages):
            yield "<think>reasoning</think>final"

        with patch("app.services.rag.RAGService._llm") as mock_llm:
            mock_llm.return_value.stream_chat = fake_stream
            events = await _collect_sse_dicts(
                stream_answer(question="Q", document_ids=[], conversation_id=22, db=db)
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
        db = _FakeAsyncSession(conversations=[SimpleNamespace(id=23, message_count=0)])

        async def fake_stream(messages):
            yield "<think>reasoning</think>the answer"

        with patch("app.services.rag.RAGService._llm") as mock_llm:
            mock_llm.return_value.stream_chat = fake_stream
            await _collect_sse_dicts(
                stream_answer(question="Q", document_ids=[], conversation_id=23, db=db)
            )

        users = [obj for obj in db.added if obj.role == "user"]
        assistants = [obj for obj in db.added if obj.role == "assistant"]
        assert len(users) == 1
        assert len(assistants) == 1
        assert assistants[0].content == "the answer"
        assert "<think>" not in assistants[0].content
        # #36：写完消息 + touch_conversation 各 commit 一次
        assert db.commits == 2
        assert db.rollbacks == 0

    @pytest.mark.asyncio
    async def test_user_message_flushed_before_streaming(self):
        """User ChatMessage is flushed (id populated) before LLM streaming starts."""
        db = _FakeAsyncSession(conversations=[SimpleNamespace(id=24, message_count=0)])

        async def fake_stream(messages):
            yield "ok"

        with patch("app.services.rag.RAGService._llm") as mock_llm:
            mock_llm.return_value.stream_chat = fake_stream
            await _collect_sse_dicts(
                stream_answer(question="Q", document_ids=[], conversation_id=24, db=db)
            )

        # Two flushes expected: one for user, one for assistant.
        assert db.flushes == 2
        users = [obj for obj in db.added if obj.role == "user"]
        assert users[0].id == 1

    @pytest.mark.asyncio
    async def test_passes_history_context_to_llm(self):
        """History rows (ordered by created_at asc) appear in messages sent to LLM."""
        history = [
            SimpleNamespace(role="user", content="earlier", document_ids=None,
                            conversation_id=25, created_at="t1"),
            SimpleNamespace(role="assistant", content="earlier reply", document_ids=None,
                            conversation_id=25, created_at="t2"),
        ]
        db = _FakeAsyncSession(history=history,
                               conversations=[SimpleNamespace(id=25, message_count=0)])
        captured_messages: list = []

        async def fake_stream(messages):
            captured_messages.extend(messages)
            yield "ok"

        with patch("app.services.rag.RAGService._llm") as mock_llm:
            mock_llm.return_value.stream_chat = fake_stream
            await _collect_sse_dicts(
                stream_answer(question="Q", document_ids=[], conversation_id=25, db=db)
            )

        # The user message and the constructed prompt are both appended.
        assert {"role": "user", "content": "earlier"} in captured_messages
        assert {"role": "assistant", "content": "earlier reply"} in captured_messages
        assert {"role": "user", "content": "Q"} in captured_messages
        # Last message is the constructed prompt ending in "general knowledge."
        assert captured_messages[-1]["role"] == "user"
        assert captured_messages[-1]["content"].startswith("Question: Q")
        assert captured_messages[-1]["content"].endswith("general knowledge.")

    @pytest.mark.asyncio
    async def test_history_filtered_by_conversation_id(self):
        """#36：stream_answer 仅看到当前会话的历史行，跨会话消息不污染 prompt。"""
        history = [
            SimpleNamespace(role="user", content="in-conv-A", document_ids=None,
                            conversation_id=30, created_at="t1"),
            SimpleNamespace(role="assistant", content="reply-A", document_ids=None,
                            conversation_id=30, created_at="t2"),
            SimpleNamespace(role="user", content="in-conv-B-should-not-leak",
                            document_ids=None, conversation_id=31, created_at="t3"),
            SimpleNamespace(role="assistant", content="reply-B-should-not-leak",
                            document_ids=None, conversation_id=31, created_at="t4"),
        ]
        db = _FakeAsyncSession(history=history,
                               conversations=[SimpleNamespace(id=30, message_count=0)])
        captured_messages: list = []

        async def fake_stream(messages):
            captured_messages.extend(messages)
            yield "ok"

        with patch("app.services.rag.RAGService._llm") as mock_llm:
            mock_llm.return_value.stream_chat = fake_stream
            await _collect_sse_dicts(
                stream_answer(question="Q", document_ids=[], conversation_id=30, db=db)
            )

        # 仅 conv 30 的两条历史可见；conv 31 不污染
        assert {"role": "user", "content": "in-conv-A"} in captured_messages
        assert {"role": "assistant", "content": "reply-A"} in captured_messages
        assert not any("conv-B" in (m.get("content") or "") for m in captured_messages)

    @pytest.mark.asyncio
    async def test_missing_conversation_yields_error_event(self):
        """#36：``stream_answer`` 在会话不存在时发出 ``event: error``，且不调 LLM。"""
        db = _FakeAsyncSession(missing_conversation_ids=[404])
        called = False

        async def fake_stream(messages):
            nonlocal called
            called = True
            yield "should-not-reach"

        with patch("app.services.rag.RAGService._llm") as mock_llm:
            mock_llm.return_value.stream_chat = fake_stream
            events = await _collect_sse_dicts(
                stream_answer(question="orphan", document_ids=[],
                              conversation_id=404, db=db)
            )

        error_events = [e for e in events if e[0] == "error"]
        assert len(error_events) == 1
        assert "404" in error_events[0][1]["error"]
        assert called is False
        assert db.added == []


class TestStreamAnswerErrorPath:
    """``stream_answer`` falls back to a single ``error`` event after rollback."""

    @pytest.mark.asyncio
    async def test_llm_failure_yields_error_event_and_rolls_back(self):
        db = _FakeAsyncSession(conversations=[SimpleNamespace(id=40, message_count=0)])

        async def fake_stream(messages):
            raise RuntimeError("boom")
            yield  # unreachable — keeps this an async generator function

        with patch("app.services.rag.RAGService._llm") as mock_llm:
            mock_llm.return_value.stream_chat = fake_stream
            events = await _collect_sse_dicts(
                stream_answer(question="Q", document_ids=[], conversation_id=40, db=db)
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
        db = _FakeAsyncSession(conversations=[SimpleNamespace(id=41, message_count=0)])

        async def fake_stream(messages):
            yield "first "
            raise RuntimeError("midway")
            yield  # unreachable

        with patch("app.services.rag.RAGService._llm") as mock_llm:
            mock_llm.return_value.stream_chat = fake_stream
            events = await _collect_sse_dicts(
                stream_answer(question="Q", document_ids=[], conversation_id=41, db=db)
            )

        # Any pre-error events should still be observable; only error after.
        assert any(e[0] == "message" for e in events)
        error_events = [e for e in events if e[0] == "error"]
        assert len(error_events) == 1
        assert "midway" in error_events[0][1]["error"]
        assert db.rollbacks == 1


class TestTouchConversationOnWrite:
    """#36：``ask`` / ``stream_answer`` 写完消息后同步会话 ``message_count`` / ``updated_at``。"""

    @pytest.mark.asyncio
    async def test_ask_calls_touch_conversation_with_delta_two(self):
        conv = SimpleNamespace(id=50, message_count=0)
        db = _FakeAsyncSession(conversations=[conv])
        fake_llm = MagicMock()
        fake_llm.chat = AsyncMock(return_value="x")

        with patch.object(chat_service, "RAGService") as MockRAG, \
             patch.object(
                 chat_service, "touch_conversation",
                 new=AsyncMock(),
             ) as mock_touch:
            mock_instance = MockRAG.return_value
            mock_instance.aretrieve = AsyncMock(return_value=[])
            mock_instance._llm = MagicMock(return_value=fake_llm)
            mock_instance._dedupe_sources = RAGService._dedupe_sources
            mock_instance._build_external_prompt = MagicMock(return_value="EXT")

            await ask(
                question="Q",
                document_ids=[],
                conversation_id=50,
                db=db,
            )

        mock_touch.assert_awaited_once()
        args, kwargs = mock_touch.call_args
        assert args[0] is db
        assert args[1] == 50
        assert kwargs.get("delta") == 2

    @pytest.mark.asyncio
    async def test_stream_answer_calls_touch_conversation_with_delta_two(self):
        db = _FakeAsyncSession(conversations=[SimpleNamespace(id=51, message_count=0)])

        async def fake_stream(messages):
            yield "ok"

        with patch("app.services.rag.RAGService._llm") as mock_llm, \
             patch.object(
                 chat_service, "touch_conversation",
                 new=AsyncMock(),
             ) as mock_touch:
            mock_llm.return_value.stream_chat = fake_stream
            await _collect_sse_dicts(
                stream_answer(question="Q", document_ids=[],
                              conversation_id=51, db=db)
            )

        mock_touch.assert_awaited_once()
        args, kwargs = mock_touch.call_args
        assert args[0] is db
        assert args[1] == 51
        assert kwargs.get("delta") == 2
