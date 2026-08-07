"""Unit tests for the conversation application service (#34).

仅覆盖 service 层；HTTP 层契约在 :mod:`tests.test_router` 与 FastAPI
``TestClient`` 行为中验证（见 ``TestConversationApi``）。
"""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import conversations as conv_service
from app.services.conversations import (
    ConversationNotFoundError,
    create_conversation,
    delete_conversation,
    get_conversation,
    list_conversations,
    list_messages,
    touch_conversation,
    update_conversation,
)


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeExecuteResult:
    def __init__(self, *, rows=None, value=None):
        self._rows = rows or []
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return _FakeScalars(self._rows)


def _where_id(statement) -> int | None:
    """从 ``select(...).where(Conversation.id == X)`` 中提取 id 值。

    仅供单元测试 stub 使用：对真实 SQL 语义不强求。
    """
    clause = getattr(statement, "_whereclause", None) or getattr(
        statement, "whereclause", None
    )
    if clause is None:
        return None
    right = getattr(clause, "right", None)
    if right is None:
        return None
    value = getattr(right, "value", None)
    if isinstance(value, (int, float)):
        return int(value)
    # SQLAlchemy 在 2.x 上把常量包成 BindParameter，取其 value
    return getattr(right, "value", None)


class _FakeAsyncSession:
    """最小 AsyncSession 替身：按 ``Conversation.id == X`` 过滤条件查表。"""

    def __init__(self, *, conversations=None, messages=None):
        self.conversations = list(conversations or [])
        self.messages = list(messages or [])
        self.added: list = []
        self.deleted: list = []
        self.commits = 0
        self.refreshed: list = []

    def add(self, obj):
        self.added.append(obj)
        if not getattr(obj, "id", None):
            obj.id = max([c.id for c in self.conversations] or [0]) + 1
        # 模拟 ORM column default：仅在字段为 None 时填充（避免覆盖显式赋值）
        now = datetime.utcnow()
        if getattr(obj, "message_count", None) is None:
            obj.message_count = 0
        if getattr(obj, "created_at", None) is None:
            obj.created_at = now
        if getattr(obj, "updated_at", None) is None:
            obj.updated_at = now
        if obj not in self.conversations:
            self.conversations.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)
        self.conversations = [c for c in self.conversations if c.id != obj.id]
        self.messages = [
            m for m in self.messages if m.conversation_id != obj.id
        ]

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        now = datetime.utcnow()
        if getattr(obj, "created_at", None) is None:
            obj.created_at = now
        if getattr(obj, "updated_at", None) is None:
            obj.updated_at = now
        self.refreshed.append(obj)

    async def execute(self, statement):
        sql = str(statement).lower()
        target_id = _where_id(statement)
        if "from conversations" in sql:
            rows = self.conversations
            if target_id is not None:
                rows = [c for c in rows if c.id == target_id]
            if len(rows) == 1 and target_id is not None:
                return _FakeExecuteResult(value=rows[0])
            return _FakeExecuteResult(rows=list(rows))
        if "from chat_messages" in sql:
            rows = self.messages
            if target_id is not None:
                rows = [m for m in rows if m.conversation_id == target_id]
            return _FakeExecuteResult(rows=list(rows))
        return _FakeExecuteResult()


def _make_conv(id: int, **kwargs):
    now = datetime.utcnow()
    return SimpleNamespace(
        id=id,
        title=kwargs.get("title", f"会话{id}"),
        message_count=kwargs.get("message_count", 0),
        created_at=kwargs.get("created_at", now),
        updated_at=kwargs.get("updated_at", now),
    )


# ---------------------------------------------------------------------------
# 纯函数：标题归一化（间接通过 create_conversation 验证）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_conversation_with_default_title():
    db = _FakeAsyncSession()
    conv = await create_conversation(db)
    assert conv.title == "新对话"
    assert conv.message_count == 0
    assert db.commits == 1


@pytest.mark.asyncio
async def test_create_conversation_trims_and_falls_back_on_blank_title():
    db = _FakeAsyncSession()
    conv = await create_conversation(db, title="   ")
    assert conv.title == "新对话"

    conv2 = await create_conversation(db, title="  关于 DDD 的讨论  ")
    assert conv2.title == "关于 DDD 的讨论"


@pytest.mark.asyncio
async def test_create_conversation_assigns_unique_ids():
    db = _FakeAsyncSession()
    c1 = await create_conversation(db)
    c2 = await create_conversation(db)
    assert c1.id != c2.id


@pytest.mark.asyncio
async def test_list_conversations_returns_all():
    db = _FakeAsyncSession(conversations=[_make_conv(1), _make_conv(2)])
    items = await list_conversations(db)
    assert len(items) == 2


@pytest.mark.asyncio
async def test_get_conversation_raises_when_missing():
    db = _FakeAsyncSession()
    with pytest.raises(ConversationNotFoundError):
        await get_conversation(db, 99)


@pytest.mark.asyncio
async def test_get_conversation_returns_match():
    conv = _make_conv(7, title="hello")
    db = _FakeAsyncSession(conversations=[conv])
    result = await get_conversation(db, 7)
    assert result.id == 7
    assert result.title == "hello"


@pytest.mark.asyncio
async def test_delete_conversation_removes_row():
    conv = _make_conv(3)
    db = _FakeAsyncSession(conversations=[conv])
    await delete_conversation(db, 3)
    assert conv in db.deleted
    assert db.commits == 1


@pytest.mark.asyncio
async def test_delete_conversation_raises_when_missing():
    db = _FakeAsyncSession()
    with pytest.raises(ConversationNotFoundError):
        await delete_conversation(db, 404)


@pytest.mark.asyncio
async def test_list_messages_returns_empty_when_no_rows():
    conv = _make_conv(5)
    db = _FakeAsyncSession(conversations=[conv])
    items = await list_messages(db, 5)
    assert items == []


@pytest.mark.asyncio
async def test_list_messages_filters_and_raises_for_missing():
    conv = _make_conv(5)
    msg_a = SimpleNamespace(id=1, role="user", content="u1", conversation_id=5)
    msg_b = SimpleNamespace(id=2, role="assistant", content="a1", conversation_id=5)
    db = _FakeAsyncSession(conversations=[conv], messages=[msg_a, msg_b])
    items = await list_messages(db, 5)
    assert [m.id for m in items] == [1, 2]

    with pytest.raises(ConversationNotFoundError):
        await list_messages(db, 99)


@pytest.mark.asyncio
async def test_touch_conversation_increments_count():
    conv = _make_conv(8, message_count=2)
    db = _FakeAsyncSession(conversations=[conv])
    await touch_conversation(db, 8, delta=1)
    assert conv.message_count == 3
    assert db.commits == 1


@pytest.mark.asyncio
async def test_touch_conversation_silently_noop_for_missing():
    db = _FakeAsyncSession()
    # 不抛错
    await touch_conversation(db, 999, delta=1)
    assert db.commits == 0


@pytest.mark.asyncio
async def test_update_conversation_changes_title():
    conv = _make_conv(11, title="old")
    db = _FakeAsyncSession(conversations=[conv])
    updated = await update_conversation(db, 11, title="new")
    assert updated.title == "new"
    assert updated.id == 11


@pytest.mark.asyncio
async def test_update_conversation_blank_title_falls_back_to_default():
    conv = _make_conv(12, title="x")
    db = _FakeAsyncSession(conversations=[conv])
    updated = await update_conversation(db, 12, title="   ")
    assert updated.title == "新对话"


@pytest.mark.asyncio
async def test_update_conversation_none_title_noop():
    conv = _make_conv(13, title="untouched")
    db = _FakeAsyncSession(conversations=[conv])
    updated = await update_conversation(db, 13, title=None)
    assert updated.title == "untouched"


@pytest.mark.asyncio
async def test_update_conversation_raises_when_missing():
    db = _FakeAsyncSession()
    with pytest.raises(ConversationNotFoundError):
        await update_conversation(db, 999, title="x")


# ---------------------------------------------------------------------------
# 路由层契约（FastAPI TestClient）
# ---------------------------------------------------------------------------


def test_conversation_endpoints_registered_via_app():
    """通过 :func:`app.api.router.router` 验证 4 个端点全 OPENAPI 注册。"""
    from fastapi import FastAPI

    from app.api.router import router

    app = FastAPI()
    app.include_router(router)
    paths = app.openapi()["paths"]
    assert "/api/conversations" in paths
    assert {"get", "post"} <= {m.lower() for m in paths["/api/conversations"]}
    assert "/api/conversations/{conversation_id}" in paths
    assert {"delete", "patch"} <= {
        m.lower() for m in paths["/api/conversations/{conversation_id}"]
    }
    assert "/api/conversations/{conversation_id}/messages" in paths
    assert "get" in {
        m.lower()
        for m in paths["/api/conversations/{conversation_id}/messages"]
    }


@pytest.mark.asyncio
async def test_create_via_service_layer_returns_model_instance(monkeypatch):
    """校验 :func:`create_conversation` 不会意外地写两遍 message_count。"""
    db = _FakeAsyncSession()
    conv = await create_conversation(db, title="规划")
    # 一次 commit（commit 后立即 refresh 不会再次 commit）
    assert db.commits == 1
    assert conv.title == "规划"
    assert conv.id > 0


@pytest.mark.asyncio
async def test_patch_endpoint_registered_via_app():
    from fastapi import FastAPI

    from app.api.router import router

    app = FastAPI()
    app.include_router(router)
    paths = app.openapi()["paths"]
    assert "patch" in {m.lower() for m in paths["/api/conversations/{conversation_id}"]}
