"""#45 聊天端点 LLM 未配置时的拒绝行为测试。"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api import chat as chat_module
from app.main import app
from app.models.schemas import ChatRequest
from app.services.rag import RAGService


client = TestClient(app)


def _patch_llm_unconfigured(monkeypatch, reason: str = "OpenAI API Key 未配置"):
    """让 ``is_llm_configured`` 在 ``app.api.chat`` 模块作用域内返回 ``(False, reason)``。"""
    monkeypatch.setattr(chat_module, "is_llm_configured", lambda: (False, reason))


@pytest.fixture(autouse=True)
def mock_rag_retrieve():
    """未配置场景不应触发 RAG 检索。"""
    with patch.object(
        RAGService, "aretrieve", new=AsyncMock(return_value=[]), create=True
    ):
        yield


class TestChatEndpointLLMUnconfigured:
    """``POST /api/chat`` 在 LLM 未配置时直接返回 503 + reason。"""

    def test_returns_503_with_reason(self, monkeypatch):
        _patch_llm_unconfigured(monkeypatch, "OpenAI API Key 未配置")

        response = client.post(
            "/api/chat",
            json={"message": "hi", "conversation_id": 1, "document_ids": []},
        )

        assert response.status_code == 503
        body = response.json()
        assert body["reason"] == "OpenAI API Key 未配置"

    def test_does_not_call_llm_or_persist_messages(self, monkeypatch):
        _patch_llm_unconfigured(monkeypatch)

        # 即便 conversation 不存在，未配置也应优先返回 503（不调 chat_service.ask）
        with patch.object(chat_module.chat_service, "ask", new=AsyncMock()) as mock_ask:
            response = client.post(
                "/api/chat",
                json={"message": "hi", "conversation_id": 999, "document_ids": []},
            )
            assert response.status_code == 503
            mock_ask.assert_not_called()


class TestChatStreamEndpointLLMUnconfigured:
    """``POST /api/chat/stream`` 在 LLM 未配置时立即产 error + done SSE。"""

    @pytest.mark.asyncio
    async def test_stream_yields_error_then_done(self, monkeypatch):
        _patch_llm_unconfigured(monkeypatch, "Anthropic Model 未配置")

        payload = ChatRequest(message="hi", document_ids=[], conversation_id=1)
        request = SimpleNamespace(headers={})

        response = await chat_module.chat_stream(request=request, payload=payload, db=object())
        events = []
        async for ev in response.body_iterator:
            events.append((ev["event"], json.loads(ev["data"])))

        # 必须先 error 再 done，且 error 携带 reason
        kinds = [e[0] for e in events]
        assert kinds == ["error", "done"]
        error_data = events[0][1]
        assert error_data["reason"] == "Anthropic Model 未配置"
        assert "error" in error_data
        # done 事件仍带 sources（与正常 done 事件保持一致）
        assert events[1][1] == {"sources": []}

    @pytest.mark.asyncio
    async def test_stream_does_not_call_llm_when_unconfigured(self, monkeypatch):
        _patch_llm_unconfigured(monkeypatch)
        with patch("app.services.rag.RAGService._llm") as mock_llm:
            payload = ChatRequest(message="hi", document_ids=[], conversation_id=1)
            request = SimpleNamespace(headers={})
            response = await chat_module.chat_stream(request=request, payload=payload, db=object())
            async for _ in response.body_iterator:
                pass
            mock_llm.assert_not_called()
