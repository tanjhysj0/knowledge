"""#79：v1 接入层白名单动态化测试——装配层推导的"当前启用全集"。

覆盖：
- 默认 settings 全开时 CHAT_STRATEGIES 为六路全量（#81 起含 graph）；
- 开关关闭后重新加载模块 → 对应策略自动退出 v1 白名单（新增检索器 +
  打开开关即自动进入 v1）；
- 端到端（经 /api/v1/chat/stream 端点驱动真实管线）：planner prompt 的
  可用策略列表 = v1 全量白名单，证据包 hit.strategy ⊆ 白名单。
"""
import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.v1 import chat as chat_module
from app.models.schemas import ChatRequest
from app.services.retrieval import assembly
from app.services.retrieval import RetrievalHit
from app.services.retrieval.agent import JUDGE_MARKER, PLAN_QUERIES_MARKER
from app.services.retrieval.planner import AVAILABLE_STRATEGIES_LINE, PLANNER_MARKER
from app.services.retrieval.reranker import RERANK_MARKER


class _FakeSession:
    """Minimal AsyncSession stand-in（同 test_chat_v2 的用法）。"""

    def __init__(self, conversation_id=99):
        self.added: list = []
        self._conversation = SimpleNamespace(id=conversation_id, message_count=0)

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
                    if not hasattr(self._conversation, "updated_at"):
                        from datetime import datetime as _dt

                        self._conversation.updated_at = _dt.utcnow()
                    return _EmptyResult(value=self._conversation)
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


class TestV1WhitelistDynamic:
    """v1 白名单由装配层按 settings 开关动态推导（非写死的策略名列表）。"""

    def test_default_full_set_matches_legacy_behavior(self):
        """默认全开：v1 白名单 = 六路全量（#81 起含 graph，与迁移前语义一致）。"""
        assert chat_module.CHAT_STRATEGIES == [
            "dense", "bm25", "entity", "event", "chapter", "graph",
        ]

    def test_tracks_switch_off_after_reload(self, monkeypatch):
        """开关关闭 → 重新加载模块后对应策略自动退出 v1 白名单。"""
        monkeypatch.setattr(assembly.settings, "retrieval_entity_enabled", False)
        monkeypatch.setattr(assembly.settings, "retrieval_chapter_enabled", False)

        reloaded = importlib.reload(chat_module)
        try:
            assert reloaded.CHAT_STRATEGIES == ["dense", "bm25", "event", "graph"]
            assert "entity" not in reloaded.CHAT_STRATEGIES
        finally:
            # 先撤销 settings 变更再重新加载，恢复默认全量白名单（避免
            # 残留收窄白名单影响后续测试）。
            monkeypatch.undo()
            importlib.reload(chat_module)


def _fake_retriever(strategy: str, hits):
    retriever = MagicMock()
    retriever.strategy = strategy
    # 显式置 None：模拟"未实现钩子"的检索器（管线应透传原始 query）。
    retriever.decorate_query = None
    retriever.retrieve = AsyncMock(return_value=hits)
    return retriever


class _V1PipelineLLM:
    """端到端证据测试的确定性 LLM：planner 返回全量六路（v1 白名单内），
    agent 第一次判定不足（触发补充检索），之后足够。"""

    def __init__(self):
        self.judge_calls = 0
        self.planner_prompts: list = []

    async def chat(self, messages, **kwargs):
        content = messages[0]["content"]
        if PLANNER_MARKER in content:
            self.planner_prompts.append(content)
            return json.dumps(
                {
                    "sub_queries": ["主查询"],
                    "entities": [],
                    "events": [],
                    "chapter_hints": [],
                    # 全量六路均建议：v1 白名单（当前启用全集）内，不越界
                    "strategies": ["dense", "bm25", "entity", "event", "chapter", "graph"],
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
        yield "v1 白名单答案"


async def _collect_sse_dicts(generator):
    events = []
    async for ev in generator:
        try:
            events.append((ev["event"], json.loads(ev["data"])))
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return events


class TestV1EvidenceStrategiesWithinWhitelist:
    """#79：端到端（经 /api/v1/chat/stream 端点）验证 planner 建议不越界、
    证据包策略 ⊆ v1 白名单。"""

    @pytest.mark.asyncio
    async def test_evidence_strategies_stay_within_whitelist(self, monkeypatch):
        fake_llm = _V1PipelineLLM()
        retrievers = {
            strategy: _fake_retriever(
                strategy, [RetrievalHit(i + 1, 0, f"{strategy}-c0", 0.9, strategy)]
            )
            for i, strategy in enumerate(["dense", "bm25", "entity", "event", "chapter", "graph"])
        }
        monkeypatch.setattr(chat_module, "is_llm_configured", lambda: (True, ""))
        from app.services.rag import RAGService

        real_aretrieve = RAGService.aretrieve
        payload = ChatRequest(message="Q", document_ids=[], conversation_id=99)
        request = MagicMock(headers={})

        with patch("app.services.rag.build_retrievers", return_value=retrievers), patch(
            "app.services.llm.get_llm_provider", return_value=fake_llm
        ), patch("app.services.rag.get_llm_provider", return_value=fake_llm), patch.object(
            RAGService, "aretrieve", new=real_aretrieve
        ):
            response = await chat_module.chat_stream(
                request=request, payload=payload, db=_FakeSession()
            )
            events = await _collect_sse_dicts(response.body_iterator)

        # planner prompt 的可用策略列表 = v1 白名单（当前启用全集）
        prompt = fake_llm.planner_prompts[0]
        line = next(
            l for l in prompt.splitlines() if l.startswith(AVAILABLE_STRATEGIES_LINE)
        )
        assert line == AVAILABLE_STRATEGIES_LINE + ", ".join(chat_module.CHAT_STRATEGIES)

        # 证据循环发生了补充检索：六路各被调用两次（首轮 + 补充）
        for strategy, retriever in retrievers.items():
            assert retriever.retrieve.await_count == 2, strategy

        # 证据事件：所有命中 strategy ⊆ v1 白名单
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
        assert "".join(message_contents) == "v1 白名单答案"
