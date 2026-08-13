"""Unit tests for database initialization (init_db) and ORM model defaults.

#62：documents 表新增 status/progress 两列，启动时轻量内联迁移自动补齐，
存量行由 ``DEFAULT`` 子句回填为 ready/100。测试用假 engine/conn 记录
执行过的 DDL 语句，不依赖真实数据库。
"""
from contextlib import asynccontextmanager

import pytest

from app.core import database
from app.models.document import Document


class _FakeConn:
    """记录 run_sync / exec_driver_sql 的假连接。"""

    def __init__(self):
        self.statements: list[str] = []

    async def run_sync(self, fn, *args, **kwargs):
        return None

    async def exec_driver_sql(self, statement):
        self.statements.append(statement)


class _FakeEngine:
    def __init__(self, conn):
        self._conn = conn

    @asynccontextmanager
    async def begin(self):
        yield self._conn


class TestDocumentModelDefaults:
    """#62：模型层列定义的默认值（INSERT 时数据库侧兜底）。"""

    def test_status_column_default_ready_not_null(self):
        col = Document.__table__.c.status
        assert col.default.arg == "ready"
        assert col.server_default.arg == "ready"
        assert not col.nullable

    def test_progress_column_default_100_not_null(self):
        col = Document.__table__.c.progress
        assert col.default.arg == 100
        assert col.server_default.arg.text == "100"
        assert not col.nullable

    def test_error_message_column_nullable_text(self):
        """#63：error_message 可空，无默认值（成功/存量记录为 NULL）。"""
        col = Document.__table__.c.error_message
        assert col.nullable
        assert col.default is None
        assert col.server_default is None


class TestInitDbMigration:
    """#62：init_db 内联迁移为 documents 表补齐 status/progress 列。"""

    @pytest.mark.asyncio
    async def test_adds_status_column_with_ready_default(self, monkeypatch):
        conn = _FakeConn()
        monkeypatch.setattr(database, "get_engine", lambda: _FakeEngine(conn))

        await database.init_db()

        joined = "\n".join(conn.statements)
        assert (
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS status" in joined
        )
        assert "DEFAULT 'ready'" in joined

    @pytest.mark.asyncio
    async def test_adds_progress_column_with_100_default(self, monkeypatch):
        conn = _FakeConn()
        monkeypatch.setattr(database, "get_engine", lambda: _FakeEngine(conn))

        await database.init_db()

        joined = "\n".join(conn.statements)
        assert (
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS progress" in joined
        )
        assert "DEFAULT 100" in joined

    @pytest.mark.asyncio
    async def test_adds_error_message_column_nullable(self, monkeypatch):
        """#63：内联迁移为 documents 表补齐可空 error_message 列。"""
        conn = _FakeConn()
        monkeypatch.setattr(database, "get_engine", lambda: _FakeEngine(conn))

        await database.init_db()

        joined = "\n".join(conn.statements)
        assert (
            "ALTER TABLE documents "
            "ADD COLUMN IF NOT EXISTS error_message TEXT" in joined
        )
