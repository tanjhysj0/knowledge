"""Unit tests for the documents application service.

覆盖上传、列表与删除逻辑；不依赖真实数据库或向量库，使用内存中的假
db Session 和 patch 替换纯 I/O 边界。
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from app.services import documents as document_service
from app.services.documents import (
    DocumentChunkError,
    DocumentEmptyError,
    DocumentNotFoundError,
    DocumentParseError,
    delete_document,
    list_documents,
    upload_document,
)


class _FakeScalarResult:
    """Simple stand-in for SQLAlchemy scalar() / scalars().all() results."""

    def __init__(self, value=None, rows=None):
        self._value = value
        self._rows = rows or []

    def scalar(self):
        return self._value

    def all(self):
        return self._rows


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeExecuteResult:
    def __init__(self, *, value=None, rows=None):
        self._value = value
        self._rows = rows or []

    def scalar(self):
        return self._value

    def scalar_one_or_none(self):
        if not self._rows:
            return None
        if len(self._rows) > 1:
            raise ValueError("multiple rows found, expected one")
        return self._rows[0]

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeAsyncSession:
    """Minimal AsyncSession stand-in supporting the operations the service uses."""

    def __init__(self, *, scalar_value=0, rows=None, missing=False):
        self.added: list = []
        self.deleted: list = []
        self.commits = 0
        self.refreshes: list = []
        self.scalar_value = scalar_value
        self.rows = [] if missing else (rows or [])
        self._next_id = 1

    def add(self, obj):
        obj.id = self._next_id
        self._next_id += 1
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        self.refreshes.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def execute(self, statement):
        sql = str(statement)
        if "count(" in sql.lower():
            return _FakeExecuteResult(value=self.scalar_value)
        # SELECT FROM documents ... ORDER BY ...
        return _FakeExecuteResult(rows=self.rows)


def _empty_session():
    """Helper for delete tests that expect a missing document."""

    class _Session(_FakeAsyncSession):
        async def execute(self, statement):
            return _FakeExecuteResult(rows=[])

    return _Session()


@pytest.fixture
def upload_dir(tmp_path, monkeypatch):
    """使用临时目录替换 settings.upload_dir。"""
    upload_path = tmp_path / "uploads"
    upload_path.mkdir()
    monkeypatch.setattr(document_service.settings, "upload_dir", str(upload_path))
    return upload_path


class TestUploadDocument:
    """上传流程：保存、解析、分块、落 DB、尝试向量库。"""

    @pytest.mark.asyncio
    async def test_success_persists_metadata_and_invokes_vector_store(self, upload_dir):
        db = _FakeAsyncSession(scalar_value=0)
        chunks = ["chunk one", "chunk two"]

        with patch.object(
            document_service.DocumentParser, "parse", return_value="text body"
        ), patch.object(
            document_service.TextChunker, "chunk", return_value=chunks
        ), patch.object(
            document_service.VectorStoreService, "insert"
        ) as mock_insert:
            document = await upload_document(
                filename="hello.txt",
                file_ext="txt",
                content=b"hello bytes",
                db=db,
            )

        assert document.filename == "hello.txt"
        assert document.file_type == "txt"
        assert document.size == len(b"hello bytes")
        assert document.chunk_count == len(chunks)
        assert db.commits == 1
        assert db.refreshes == [document]
        assert (upload_dir / "hello.txt").read_bytes() == b"hello bytes"
        mock_insert.assert_called_once_with(
            document_id=document.id,
            chunks=chunks,
            embeddings=[[]] * len(chunks),
        )

    @pytest.mark.asyncio
    async def test_parse_failure_raises_service_error(self, upload_dir):
        db = _FakeAsyncSession()

        with patch.object(
            document_service.DocumentParser,
            "parse",
            side_effect=ValueError("boom"),
        ):
            with pytest.raises(DocumentParseError) as exc:
                await upload_document(
                    filename="bad.txt",
                    file_ext="txt",
                    content=b"x",
                    db=db,
                )
        assert "boom" in str(exc.value)

    @pytest.mark.asyncio
    async def test_empty_content_raises_empty_error(self, upload_dir):
        db = _FakeAsyncSession()

        with patch.object(
            document_service.DocumentParser, "parse", return_value=""
        ):
            with pytest.raises(DocumentEmptyError):
                await upload_document(
                    filename="empty.txt",
                    file_ext="txt",
                    content=b"",
                    db=db,
                )

    @pytest.mark.asyncio
    async def test_whitespace_only_content_raises_empty_error(self, upload_dir):
        db = _FakeAsyncSession()

        with patch.object(
            document_service.DocumentParser, "parse", return_value="   \n\t "
        ):
            with pytest.raises(DocumentEmptyError):
                await upload_document(
                    filename="ws.txt",
                    file_ext="txt",
                    content=b" ",
                    db=db,
                )

    @pytest.mark.asyncio
    async def test_no_chunks_raises_chunk_error(self, upload_dir):
        db = _FakeAsyncSession()

        with patch.object(
            document_service.DocumentParser, "parse", return_value="text"
        ), patch.object(
            document_service.TextChunker, "chunk", return_value=[]
        ):
            with pytest.raises(DocumentChunkError):
                await upload_document(
                    filename="nothing.txt",
                    file_ext="txt",
                    content=b"text",
                    db=db,
                )

    @pytest.mark.asyncio
    async def test_vector_store_failure_is_swallowed(self, upload_dir):
        db = _FakeAsyncSession()
        chunks = ["c1"]

        with patch.object(
            document_service.DocumentParser, "parse", return_value="text"
        ), patch.object(
            document_service.TextChunker, "chunk", return_value=chunks
        ), patch.object(
            document_service.VectorStoreService,
            "insert",
            side_effect=RuntimeError("milvus down"),
        ):
            document = await upload_document(
                filename="ok.txt",
                file_ext="txt",
                content=b"text",
                db=db,
            )

        # 元数据依然落库成功，向量库异常被静默忽略（既有契约）。
        assert document.id == 1
        assert db.commits == 1


class TestListDocuments:
    """列表流程：分页归一化、总数查询、结果集组装。"""

    @pytest.mark.asyncio
    async def test_returns_empty_response_when_no_documents(self):
        db = _FakeAsyncSession(scalar_value=0, rows=[])

        response = await list_documents(db)

        assert response.total == 0
        assert response.items == []
        assert response.total_pages == 0
        assert response.page == 1
        assert response.page_size == 10

    @pytest.mark.asyncio
    async def test_returns_documents_with_correct_pagination(self):
        rows = [
            SimpleNamespace(
                id=i,
                filename=f"doc-{i}.txt",
                file_type="txt",
                size=10 * i,
                file_path=f"/uploads/doc-{i}.txt",
                chunk_count=i,
                created_at=f"2026-08-0{i}",
            )
            for i in range(1, 4)
        ]
        db = _FakeAsyncSession(scalar_value=10, rows=rows)

        response = await list_documents(db, page=2, page_size=3)

        assert response.total == 10
        assert response.page == 2
        assert response.page_size == 3
        assert response.total_pages == 4  # ceil(10 / 3)
        assert [item.id for item in response.items] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_normalizes_negative_and_zero_inputs(self):
        db = _FakeAsyncSession(scalar_value=0, rows=[])

        response = await list_documents(db, page=-3, page_size=0)

        assert response.page == 1
        assert response.page_size == 10

    @pytest.mark.asyncio
    async def test_caps_page_size_at_100(self):
        db = _FakeAsyncSession(scalar_value=0, rows=[])

        response = await list_documents(db, page=1, page_size=500)

        assert response.page_size == 100


class TestDeleteDocument:
    """删除流程：查找、向量库清理、磁盘文件删除、DB 记录删除。"""

    @pytest.mark.asyncio
    async def test_raises_not_found_when_missing(self):
        db = _FakeAsyncSession(scalar_value=0)

        with pytest.raises(DocumentNotFoundError):
            await delete_document(db, document_id=42)

    @pytest.mark.asyncio
    async def test_success_removes_vector_disk_and_db(self, upload_dir):
        file_path = upload_dir / "to-delete.txt"
        file_path.write_text("content")
        document = SimpleNamespace(id=7, file_path=str(file_path))
        db = _FakeAsyncSession(scalar_value=0, rows=[document])

        with patch.object(
            document_service.VectorStoreService, "delete_by_document_id"
        ) as mock_delete_vec:
            await delete_document(db, document_id=7)

        assert not file_path.exists()
        assert db.deleted == [document]
        assert db.commits == 1
        mock_delete_vec.assert_called_once_with(7)

    @pytest.mark.asyncio
    async def test_missing_disk_file_does_not_raise(self, upload_dir):
        document = SimpleNamespace(id=8, file_path=str(upload_dir / "ghost.txt"))
        db = _FakeAsyncSession(scalar_value=0, rows=[document])

        with patch.object(
            document_service.VectorStoreService, "delete_by_document_id"
        ):
            await delete_document(db, document_id=8)

        assert db.deleted == [document]
        assert db.commits == 1

    @pytest.mark.asyncio
    async def test_vector_store_failure_is_swallowed(self, upload_dir):
        file_path = upload_dir / "another.txt"
        file_path.write_text("content")
        document = SimpleNamespace(id=9, file_path=str(file_path))
        db = _FakeAsyncSession(scalar_value=0, rows=[document])

        with patch.object(
            document_service.VectorStoreService,
            "delete_by_document_id",
            side_effect=RuntimeError("milvus down"),
        ):
            await delete_document(db, document_id=9)

        # 磁盘文件和 DB 记录仍然被清理。
        assert not file_path.exists()
        assert db.deleted == [document]
        assert db.commits == 1
