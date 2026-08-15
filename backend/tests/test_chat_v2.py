"""#77 v2 聊天端点（``POST /api/v2/chat`` 与 ``POST /api/v2/chat/stream``）
契约测试。

覆盖（先例：test_chat_unconfigured.py / test_chat_stream.py 的 client 与
endpoint 注入模式）：
- 503 preflight：LLM 未配置时非流式返回 503 JSON、流式先 error 再 done；
- 404：会话不存在时非流式抛 404、流式产单条 error 事件；
- SSE 事件形状：thinking / message / done（sources）与 v1 一致；
- 子集检索策略白名单透传：接入层固定传入 ``["dense", "bm25"]``；
- 证据包策略不越界：端到端驱动真实管线（注入伪检索器），并行检索与
  证据循环补充检索只调用白名单内策略，证据事件 ``hit.strategy`` ⊆ 白名单。
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.v2 import chat as chat_module
from app.main import app
from app.models.schemas import ChatRequest
from app.services import chat as chat_service
from app.services.conversations import ConversationNotFoundError
from app.services.rag import RAGService
from app.services.retrieval import RetrievalHit
from app.services.retrieval.agent import JUDGE_MARKER, PLAN_QUERIES_MARKER
from app.services.retrieval.planner import PLANNER_MARKER
from app.services.retrieval.reranker import RERANK_MARKER

client = TestClient(app)

# 模块加载时（autouse fixture 生效前）捕获的真实实现，供证据不越界测试
# 在 aretrieve 被默认 mock 的情况下恢复真实检索链路。
_REAL_ARETRIEVE = RAGService.aretrieve


def _patch_llm_unconfigured(monkeypatch, reason: str = "OpenAI API Key 未配置"):
    """让 ``is_llm_configured`` 在 ``app.api.v2.chat`` 模块作用域内返回 ``(False, reason)``。"""
    monkeypatch.setattr(chat_module, "is_llm_configured", lambda: (False, reason))


@pytest.fixture(autouse=True)
def mock_rag_retrieve():
    """默认让 :meth:`RAGService.aretrieve` 返回 ``[]``（不加载 bge-m3）。

    SSE 形状 / 白名单透传测试不需要真检索；证据不越界测试显式 patch 覆盖。
    """
    with patch.object(
        RAGService, "aretrieve", new=AsyncMock(return_value=[]), create=True
    ):
        yield


@pytest.fixture(autouse=True)
def pass_llm_preflight(monkeypatch):
    """``#45`` 默认让 ``is_llm_configured`` 在 ``app.api.v2.chat`` 作用域内通过，
    避免测试被 preflight 提前拒绝。验证 preflight 的测试在显式 patch 中覆盖。
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
    """Minimal AsyncSession stand-in used as the v2 chat_stream db dependency."""

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


def _fake_db(conversation_id=99, missing=False):
    return _FakeSession(
        conversations=[SimpleNamespace(id=conversation_id, message_count=0)],
        missing_conversation_ids=[conversation_id] if missing else None,
    )


# ---------------------------------------------------------------- 503 preflight

class TestV2ChatEndpointLLMUnconfigured:
    """``POST /api/v2/chat`` 在 LLM 未配置时直接返回 503 + reason（与 v1 一致）。"""

    def test_returns_503_with_reason(self, monkeypatch):
        _patch_llm_unconfigured(monkeypatch, "OpenAI API Key 未配置")

        response = client.post(
            "/api/v2/chat",
            json={"message": "hi", "conversation_id": 1, "document_ids": []},
        )

        assert response.status_code == 503
        assert response.json()["reason"] == "OpenAI API Key 未配置"

    def test_e2e_mock_request_still_rejected_when_unconfigured(self, monkeypatch):
        """preflight 无条件执行：E2E mock 请求（X-E2E-Test）在 LLM 未配置时同样 503。"""
        _patch_llm_unconfigured(monkeypatch)

        with patch.object(chat_service, "ask", new=AsyncMock()) as mock_ask:
            response = client.post(
                "/api/v2/chat",
                headers={"X-E2E-Test": "true"},
                json={"message": "hi", "conversation_id": 1, "document_ids": []},
            )
            assert response.status_code == 503
            assert response.json()["reason"] == "OpenAI API Key 未配置"
            mock_ask.assert_not_called()

    def test_does_not_call_ask_or_persist_messages(self, monkeypatch):
        _patch_llm_unconfigured(monkeypatch)

        # 即便 conversation 不存在，未配置也应优先返回 503（不调 chat_service.ask）
        with patch.object(chat_service, "ask", new=AsyncMock()) as mock_ask:
            response = client.post(
                "/api/v2/chat",
                json={"message": "hi", "conversation_id": 999, "document_ids": []},
            )
            assert response.status_code == 503
            mock_ask.assert_not_called()


class TestV2ChatStreamEndpointLLMUnconfigured:
    """``POST /api/v2/chat/stream`` 在 LLM 未配置时立即产 error + done SSE。"""

    @pytest.mark.asyncio
    async def test_stream_yields_error_then_done(self, monkeypatch):
        _patch_llm_unconfigured(monkeypatch, "Anthropic Model 未配置")

        payload = ChatRequest(message="hi", document_ids=[], conversation_id=1)
        request = SimpleNamespace(headers={})

        response = await chat_module.chat_stream(request=request, payload=payload, db=object())
        events = []
        async for ev in response.body_iterator:
            events.append((ev["event"], json.loads(ev["data"])))

        # 必须先 error 再 done，且 error 携带 reason（与 v1 形状一致）
        kinds = [e[0] for e in events]
        assert kinds == ["error", "done"]
        error_data = events[0][1]
        assert error_data["reason"] == "Anthropic Model 未配置"
        assert "error" in error_data
        assert events[1][1] == {"sources": []}

    @pytest.mark.asyncio
    async def test_e2e_mock_request_still_rejected_when_unconfigured(self, monkeypatch):
        """preflight 无条件执行：E2E mock 请求在 LLM 未配置时同样产 error + done。"""
        _patch_llm_unconfigured(monkeypatch)

        payload = ChatRequest(message="hi", document_ids=[], conversation_id=1)
        request = SimpleNamespace(headers={"x-e2e-test": "true"})

        with patch.object(chat_service, "stream_answer") as mock_stream:
            response = await chat_module.chat_stream(request=request, payload=payload, db=object())
            events = []
            async for ev in response.body_iterator:
                events.append((ev["event"], json.loads(ev["data"])))

            kinds = [e[0] for e in events]
            assert kinds == ["error", "done"]
            assert events[0][1]["reason"] == "OpenAI API Key 未配置"
            mock_stream.assert_not_called()


# ---------------------------------------------------------------- 404 会话不存在

class TestV2ChatEndpointNotFound:
    """``POST /api/v2/chat`` 在会话不存在时返回 404（与 v1 一致）。"""

    @pytest.mark.asyncio
    async def test_missing_conversation_returns_404(self):
        payload = ChatRequest(message="Hi", document_ids=[], conversation_id=999)
        request = MagicMock(headers={})

        with patch.object(
            chat_service,
            "ask",
            new=AsyncMock(side_effect=ConversationNotFoundError("会话不存在")),
        ) as mock_ask:
            with pytest.raises(HTTPException) as exc:
                await chat_module.chat(request=request, payload=payload, db=object())

        assert exc.value.status_code == 404
        mock_ask.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stream_missing_conversation_yields_error_event(self):
        """流式端点在会话不存在时产单条 error（携带会话不存在语义），不调 LLM。"""
        called = False

        async def fake_stream(messages):
            nonlocal called
            called = True
            yield "should-not-reach"

        with patch("app.services.rag.RAGService._llm") as mock_llm:
            mock_llm.return_value.stream_chat = fake_stream
            payload = ChatRequest(message="Hi", document_ids=[], conversation_id=12345)
            request = MagicMock(headers={})
            response = await chat_module.chat_stream(
                request=request, payload=payload,
                db=_fake_db(conversation_id=12345, missing=True),
            )
            events = await _collect_sse_dicts(response.body_iterator)

        error_events = [e for e in events if e[0] == "error"]
        assert len(error_events) == 1
        # 断言错误语义与具体会话 id，不依赖魔数（如 404）拼进消息
        assert "not found" in error_events[0][1]["error"]
        assert "12345" in error_events[0][1]["error"]
        assert called is False


# ---------------------------------------------------------------- 白名单透传

class TestV2EndpointStrategyWhitelist:
    """#77：v2 端点接入层固定传入子集检索策略白名单（请求体与 v1 相同）。"""

    def test_module_level_constant_is_subset_of_v1(self):
        """白名单为模块级常量，且是 v1 全量白名单的子集。"""
        from app.api.v1.chat import CHAT_STRATEGIES as V1_STRATEGIES

        assert chat_module.CHAT_STRATEGIES == ["dense", "bm25"]
        assert set(chat_module.CHAT_STRATEGIES) <= set(V1_STRATEGIES)

    @pytest.mark.asyncio
    async def test_chat_endpoint_passes_subset_whitelist(self):
        payload = ChatRequest(message="Hi", document_ids=[], conversation_id=99)
        request = MagicMock(headers={})

        with patch.object(
            chat_service,
            "ask",
            new=AsyncMock(return_value={"answer": "ok", "sources": []}),
        ) as mock_ask:
            await chat_module.chat(request=request, payload=payload, db=_fake_db())

        mock_ask.assert_called_once()
        _, kwargs = mock_ask.call_args
        assert kwargs["strategies"] == ["dense", "bm25"]

    @pytest.mark.asyncio
    async def test_stream_endpoint_passes_subset_whitelist(self):
        async def fake_stream(**kwargs):
            yield {"event": "done", "data": json.dumps({"sources": []})}

        payload = ChatRequest(message="Hi", document_ids=[], conversation_id=99)
        request = MagicMock(headers={})

        with patch.object(
            chat_service, "stream_answer", side_effect=fake_stream
        ) as mock_stream:
            response = await chat_module.chat_stream(
                request=request, payload=payload, db=_fake_db()
            )
            await _collect_sse_dicts(response.body_iterator)

        mock_stream.assert_called_once()
        _, kwargs = mock_stream.call_args
        assert kwargs["strategies"] == ["dense", "bm25"]


# ---------------------------------------------------------------- SSE 事件形状

class TestV2ChatStreamSSE:
    """经 /api/v2/chat/stream 端点驱动真 ``stream_answer``，SSE 形状与 v1 一致。"""

    async def _drive(self, llm_chunks, conversation_id=99):
        async def fake_stream(messages):
            for chunk in llm_chunks:
                yield chunk

        with patch("app.services.rag.RAGService._llm") as mock_llm:
            mock_llm.return_value.stream_chat = fake_stream
            payload = ChatRequest(
                message="Hi", document_ids=[], conversation_id=conversation_id
            )
            request = MagicMock(headers={})
            response = await chat_module.chat_stream(
                request=request, payload=payload, db=_fake_db(conversation_id)
            )
            return await _collect_sse_dicts(response.body_iterator)

    @pytest.mark.asyncio
    async def test_stream_emits_only_message_events_for_plain_answer(self):
        events = await self._drive(["Hello", " world", "!"])
        message_events = [e for e in events if e[0] == "message"]
        assert message_events == [
            ("message", {"content": "Hello"}),
            ("message", {"content": " world"}),
            ("message", {"content": "!"}),
        ]
        assert [e[0] for e in events if e[0] == "thinking"] == []

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
    async def test_done_event_has_sources(self):
        events = await self._drive(["Hi"])
        done = next((e for e in events if e[0] == "done"), None)
        assert done is not None
        assert done[1] == {"sources": [], "evidence": []}


# ---------------------------------------------------------------- 证据包策略不越界

class _V2PipelineLLM:
    """端到端证据测试的确定性 LLM：planner 建议含白名单外策略（entity），
    agent 第一次判定不足（触发补充检索），之后足够。"""

    def __init__(self):
        self.judge_calls = 0

    async def chat(self, messages, **kwargs):
        content = messages[0]["content"]
        if PLANNER_MARKER in content:
            return json.dumps(
                {
                    "sub_queries": ["主查询"],
                    "entities": [],
                    "events": [],
                    "chapter_hints": [],
                    # entity 在白名单外：planner 建议它，白名单应将其过滤
                    "strategies": ["dense", "bm25", "entity"],
                },
                ensure_ascii=False,
            )
        if PLAN_QUERIES_MARKER in content:
            return '{"queries": ["补充查询"]}'
        if JUDGE_MARKER in content:
            self.judge_calls += 1
            return "INSUFFICIENT" if self.judge_calls == 1 else "SUFFICIENT"
        if RERANK_MARKER in content:
            return '{"order": [], "reject": []}'
        raise AssertionError(f"unexpected prompt: {content[:80]!r}")

    async def stream_chat(self, messages):
        yield "v2 白名单答案"


def _fake_retriever(strategy: str, hits):
    retriever = MagicMock()
    retriever.strategy = strategy
    # 显式置 None：模拟“未实现钩子”的检索器（管线应透传原始 query）。
    retriever.decorate_query = None
    retriever.retrieve = AsyncMock(return_value=hits)
    return retriever


class TestV2EvidenceStrategiesWithinWhitelist:
    """#77：端到端（经 /api/v2/chat/stream 端点）验证证据包策略不越界。

    注入含白名单外检索器（entity）的伪检索器集合驱动真实混合检索管线：
    并行检索一步与证据循环补充检索都只调用白名单内策略（dense / bm25），
    证据事件与 done.evidence 的 ``hit.strategy`` 集合 ⊆ v2 白名单。
    """

    @pytest.mark.asyncio
    async def test_evidence_strategies_stay_within_whitelist(self):
        dense = _fake_retriever(
            "dense", [RetrievalHit(1, 0, "d0", 0.9, "dense")]
        )
        bm25 = _fake_retriever(
            "bm25", [RetrievalHit(1, 1, "b0", 0.8, "bm25")]
        )
        entity = _fake_retriever(
            "entity", [RetrievalHit(2, 0, "e0", 0.7, "entity")]
        )
        fake_llm = _V2PipelineLLM()

        payload = ChatRequest(message="Q", document_ids=[], conversation_id=99)
        request = MagicMock(headers={})

        with patch(
            "app.services.rag.build_retrievers",
            return_value={"dense": dense, "bm25": bm25, "entity": entity},
        ), patch(
            "app.services.llm.get_llm_provider", return_value=fake_llm
        ), patch(
            "app.services.rag.get_llm_provider", return_value=fake_llm
        ), patch.object(
            RAGService, "aretrieve", new=_REAL_ARETRIEVE
        ):
            response = await chat_module.chat_stream(
                request=request, payload=payload, db=_fake_db()
            )
            events = await _collect_sse_dicts(response.body_iterator)

        # 证据循环发生了补充检索：dense / bm25 各被调用两次（首轮 + 补充）
        assert dense.retrieve.await_count == 2
        assert bm25.retrieve.await_count == 2
        # 白名单外策略从未被调用（含证据循环补充检索）
        entity.retrieve.assert_not_awaited()

        # 证据事件：所有命中 strategy ⊆ v2 白名单
        evidence_events = [e for e in events if e[0] == "evidence"]
        assert evidence_events, "expected at least one evidence event"
        strategies = {
            hit["strategy"] for ev in evidence_events for hit in ev[1]["hits"]
        }
        assert strategies <= set(chat_module.CHAT_STRATEGIES)

        # done 事件的结构化证据同样不越界
        done = next(e for e in events if e[0] == "done")
        done_strategies = {hit["strategy"] for hit in done[1]["evidence"]}
        assert done_strategies <= set(chat_module.CHAT_STRATEGIES)

        # 回答正常产出（message 事件非空）
        message_contents = [e[1]["content"] for e in events if e[0] == "message"]
        assert "".join(message_contents) == "v2 白名单答案"
