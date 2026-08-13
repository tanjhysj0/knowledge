"""Unit tests for the documents application service.

覆盖上传、列表与删除逻辑；不依赖真实数据库或向量库，使用内存中的假
db Session 和 patch 替换纯 I/O 边界。
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from app.services import documents as document_service
from app.services.documents import (
    CoverTooLargeError,
    CoverTypeError,
    DocumentChunkError,
    DocumentEmbeddingError,
    DocumentEmptyError,
    DocumentNotFoundError,
    DocumentParseError,
    DocumentTitleError,
    delete_document,
    get_document,
    list_documents,
    update_document,
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

    async def flush(self):
        self.flushes = getattr(self, "flushes", 0) + 1

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


@pytest.fixture
def cover_dir(tmp_path, monkeypatch):
    """使用临时目录替换 settings.cover_dir（#48）。"""
    cover_path = tmp_path / "covers"
    cover_path.mkdir()
    monkeypatch.setattr(document_service.settings, "cover_dir", str(cover_path))
    return cover_path


class TestUploadDocument:
    """上传流程：保存、解析、分块、落 DB、尝试向量库。"""

    @pytest.mark.asyncio
    async def test_success_persists_metadata_and_invokes_vector_store(self, upload_dir):
        db = _FakeAsyncSession(scalar_value=0)
        chunks = ["chunk one", "chunk two"]
        fake_embeddings = [[0.1, 0.2], [0.3, 0.4]]

        with patch.object(
            document_service.DocumentParser, "parse", return_value="text body"
        ), patch.object(
            document_service.TextChunker, "chunk", return_value=chunks
        ), patch.object(
            document_service, "get_embedding_provider"
        ) as mock_get_provider, patch.object(
            document_service.VectorStoreService, "insert"
        ) as mock_insert, patch.object(
            document_service.VectorStoreService, "__init__", return_value=None
        ):
            mock_provider = MagicMock()
            mock_provider.embed_texts = MagicMock(return_value=fake_embeddings)
            mock_get_provider.return_value = mock_provider

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
        # 验证 embedding provider 被调用，且 chunks 原样下传
        mock_provider.embed_texts.assert_called_once_with(chunks)
        # 验证 insert 收到真 embeddings（不是空 list）
        mock_insert.assert_called_once_with(
            document_id=document.id,
            chunks=chunks,
            embeddings=fake_embeddings,
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
    async def test_embedding_provider_failure_raises_service_error(self, upload_dir):
        """``embed_texts`` 抛异常时必须以 ``DocumentEmbeddingError`` 向上抛（#31）。"""
        db = _FakeAsyncSession()
        chunks = ["c1", "c2"]

        with patch.object(
            document_service.DocumentParser, "parse", return_value="text"
        ), patch.object(
            document_service.TextChunker, "chunk", return_value=chunks
        ), patch.object(
            document_service, "get_embedding_provider"
        ) as mock_get_provider, patch.object(
            document_service.VectorStoreService, "insert"
        ) as mock_insert:
            mock_provider = MagicMock()
            mock_provider.embed_texts = MagicMock(
                side_effect=RuntimeError("model down")
            )
            mock_get_provider.return_value = mock_provider

            with pytest.raises(DocumentEmbeddingError) as exc:
                await upload_document(
                    filename="ok.txt",
                    file_ext="txt",
                    content=b"text",
                    db=db,
                )

        assert "model down" in str(exc.value)
        # metadata 仍然落库（PG commit 已发生）；向量库未被调用
        assert db.commits == 1
        mock_insert.assert_not_called()

    @pytest.mark.asyncio
    async def test_vector_store_insert_failure_raises_service_error(self, upload_dir):
        """``vector_store.insert`` 抛异常时也必须冒泡（#31）。"""
        db = _FakeAsyncSession()
        chunks = ["c1"]
        fake_embeddings = [[0.5, 0.6]]

        with patch.object(
            document_service.DocumentParser, "parse", return_value="text"
        ), patch.object(
            document_service.TextChunker, "chunk", return_value=chunks
        ), patch.object(
            document_service, "get_embedding_provider"
        ) as mock_get_provider, patch.object(
            document_service.VectorStoreService, "__init__", return_value=None
        ), patch.object(
            document_service.VectorStoreService,
            "insert",
            side_effect=RuntimeError("milvus down"),
        ):
            mock_provider = MagicMock()
            mock_provider.embed_texts = MagicMock(return_value=fake_embeddings)
            mock_get_provider.return_value = mock_provider

            with pytest.raises(DocumentEmbeddingError) as exc:
                await upload_document(
                    filename="ok.txt",
                    file_ext="txt",
                    content=b"text",
                    db=db,
                )

        assert "milvus down" in str(exc.value)


class TestUploadDocumentTitle:
    """#53：上传时的小说名（title）处理。"""

    async def _upload(self, db, *, filename, title=None):
        chunks = ["chunk one"]
        fake_embeddings = [[0.1, 0.2]]

        with patch.object(
            document_service.DocumentParser, "parse", return_value="text body"
        ), patch.object(
            document_service.TextChunker, "chunk", return_value=chunks
        ), patch.object(
            document_service, "get_embedding_provider"
        ) as mock_get_provider, patch.object(
            document_service.VectorStoreService, "insert"
        ), patch.object(
            document_service.VectorStoreService, "__init__", return_value=None
        ):
            mock_provider = MagicMock()
            mock_provider.embed_texts = MagicMock(return_value=fake_embeddings)
            mock_get_provider.return_value = mock_provider

            return await upload_document(
                filename=filename,
                file_ext="txt",
                content=b"novel bytes",
                db=db,
                title=title,
            )

    @pytest.mark.asyncio
    async def test_upload_with_title_strips_and_uses_it(self, upload_dir):
        db = _FakeAsyncSession(scalar_value=0)

        document = await self._upload(db, filename="x.txt", title="  十日终焉  ")

        assert document.title == "十日终焉"

    @pytest.mark.asyncio
    async def test_upload_without_title_falls_back_to_filename(self, upload_dir):
        db = _FakeAsyncSession(scalar_value=0)

        document = await self._upload(db, filename="十日终焉.txt", title=None)

        assert document.title == "十日终焉"

    @pytest.mark.asyncio
    async def test_upload_with_blank_title_falls_back_to_filename(self, upload_dir):
        db = _FakeAsyncSession(scalar_value=0)

        document = await self._upload(db, filename="novel.txt", title="   ")

        assert document.title == "novel"


class TestUploadDocumentCover:
    """#48：双文件上传（正文 + 可选封面）。"""

    async def _upload(
        self,
        db,
        *,
        cover_content=None,
        cover_ext=None,
    ):
        chunks = ["chunk one"]
        fake_embeddings = [[0.1, 0.2]]

        with patch.object(
            document_service.DocumentParser, "parse", return_value="text body"
        ), patch.object(
            document_service.TextChunker, "chunk", return_value=chunks
        ), patch.object(
            document_service, "get_embedding_provider"
        ) as mock_get_provider, patch.object(
            document_service.VectorStoreService, "insert"
        ), patch.object(
            document_service.VectorStoreService, "__init__", return_value=None
        ):
            mock_provider = MagicMock()
            mock_provider.embed_texts = MagicMock(return_value=fake_embeddings)
            mock_get_provider.return_value = mock_provider

            return await upload_document(
                filename="novel.txt",
                file_ext="txt",
                content=b"novel bytes",
                db=db,
                cover_content=cover_content,
                cover_ext=cover_ext,
            )

    @pytest.mark.asyncio
    async def test_upload_with_cover_sets_cover_image_path(self, upload_dir, cover_dir):
        db = _FakeAsyncSession(scalar_value=0)

        document = await self._upload(
            db, cover_content=b"\x89PNG fake", cover_ext="png"
        )

        assert document.cover_image_path == f"covers/{document.id}.png"
        assert (cover_dir / f"{document.id}.png").read_bytes() == b"\x89PNG fake"
        assert db.commits == 1

    @pytest.mark.asyncio
    async def test_upload_without_cover_keeps_cover_image_path_none(self, upload_dir, cover_dir):
        db = _FakeAsyncSession(scalar_value=0)

        document = await self._upload(db)

        assert document.cover_image_path is None
        assert list(cover_dir.iterdir()) == []

    @pytest.mark.asyncio
    async def test_invalid_cover_ext_raises_and_does_not_pollute(
        self, upload_dir, cover_dir
    ):
        db = _FakeAsyncSession(scalar_value=0)

        with pytest.raises(CoverTypeError):
            await self._upload(db, cover_content=b"gif", cover_ext="gif")

        # 前置校验失败：不写主文件、不落库（#48）
        assert db.added == []
        assert db.commits == 0
        assert list(upload_dir.iterdir()) == []

    @pytest.mark.asyncio
    async def test_oversized_cover_raises(
        self, upload_dir, cover_dir, monkeypatch
    ):
        monkeypatch.setattr(document_service.settings, "cover_max_size", 10)
        db = _FakeAsyncSession(scalar_value=0)

        with pytest.raises(CoverTooLargeError):
            await self._upload(db, cover_content=b"x" * 11, cover_ext="png")

        assert db.added == []
        assert list(upload_dir.iterdir()) == []


class TestUpdateDocument:
    """#53：编辑小说——改小说名与换封面。"""

    def _make_doc(self, **kwargs):
        defaults = dict(
            id=3,
            filename="novel.txt",
            file_type="txt",
            size=100,
            file_path="/uploads/novel.txt",
            chunk_count=2,
            title="旧名",
            cover_image_path=None,
        )
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    @pytest.mark.asyncio
    async def test_update_title_only_strips_and_saves(self, cover_dir):
        doc = self._make_doc()
        db = _FakeAsyncSession(scalar_value=0, rows=[doc])

        updated = await update_document(db, document_id=3, title="  新名  ")

        assert updated.title == "新名"
        assert doc.title == "新名"
        assert db.commits == 1

    @pytest.mark.asyncio
    async def test_update_cover_same_ext_overwrites_file(self, cover_dir):
        (cover_dir / "3.png").write_bytes(b"old png")
        doc = self._make_doc(cover_image_path="covers/3.png")
        db = _FakeAsyncSession(scalar_value=0, rows=[doc])

        updated = await update_document(
            db, document_id=3, cover_content=b"\x89PNG new", cover_ext="png"
        )

        assert updated.cover_image_path == "covers/3.png"
        assert (cover_dir / "3.png").read_bytes() == b"\x89PNG new"

    @pytest.mark.asyncio
    async def test_update_cover_ext_change_cleans_old_file(self, cover_dir):
        old_cover = cover_dir / "3.png"
        old_cover.write_bytes(b"old png")
        doc = self._make_doc(cover_image_path="covers/3.png")
        db = _FakeAsyncSession(scalar_value=0, rows=[doc])

        updated = await update_document(
            db, document_id=3, cover_content=b"\x89PNG new", cover_ext="jpg"
        )

        assert updated.cover_image_path == "covers/3.jpg"
        assert not old_cover.exists()
        assert (cover_dir / "3.jpg").read_bytes() == b"\x89PNG new"

    @pytest.mark.asyncio
    async def test_update_title_and_cover_together(self, cover_dir):
        doc = self._make_doc()
        db = _FakeAsyncSession(scalar_value=0, rows=[doc])

        updated = await update_document(
            db,
            document_id=3,
            title="新名",
            cover_content=b"\x89PNG new",
            cover_ext="png",
        )

        assert updated.title == "新名"
        assert updated.cover_image_path == "covers/3.png"

    @pytest.mark.asyncio
    async def test_empty_edit_raises_title_error(self, cover_dir):
        db = _FakeAsyncSession(scalar_value=0, rows=[self._make_doc()])

        with pytest.raises(DocumentTitleError):
            await update_document(db, document_id=3)

        assert db.commits == 0

    @pytest.mark.asyncio
    async def test_blank_title_raises_title_error(self, cover_dir):
        db = _FakeAsyncSession(scalar_value=0, rows=[self._make_doc()])

        with pytest.raises(DocumentTitleError):
            await update_document(db, document_id=3, title="   ")

        assert db.commits == 0

    @pytest.mark.asyncio
    async def test_missing_document_raises_not_found(self, cover_dir):
        db = _FakeAsyncSession(scalar_value=0, missing=True)

        with pytest.raises(DocumentNotFoundError):
            await update_document(db, document_id=99, title="x")

    @pytest.mark.asyncio
    async def test_invalid_cover_ext_raises_type_error(self, cover_dir):
        db = _FakeAsyncSession(scalar_value=0, rows=[self._make_doc()])

        with pytest.raises(CoverTypeError):
            await update_document(
                db, document_id=3, cover_content=b"gif", cover_ext="gif"
            )

        assert db.commits == 0


class TestGetDocument:
    """单文档详情：管理端编辑页按 id 拉取预填数据。"""

    def _make_doc(self, **kwargs):
        defaults = dict(
            id=3,
            filename="novel.txt",
            file_type="txt",
            size=100,
            file_path="/uploads/novel.txt",
            chunk_count=2,
            title="十日终焉",
            cover_image_path="covers/3.png",
        )
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    @pytest.mark.asyncio
    async def test_returns_document_when_found(self):
        doc = self._make_doc()
        db = _FakeAsyncSession(scalar_value=0, rows=[doc])

        result = await get_document(db, document_id=3)

        assert result is doc
        assert result.title == "十日终焉"
        assert result.cover_image_path == "covers/3.png"

    @pytest.mark.asyncio
    async def test_missing_document_raises_not_found(self):
        db = _FakeAsyncSession(scalar_value=0, missing=True)

        with pytest.raises(DocumentNotFoundError):
            await get_document(db, document_id=99)


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
        document = SimpleNamespace(id=7, file_path=str(file_path), cover_image_path=None)
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
        document = SimpleNamespace(
            id=8, file_path=str(upload_dir / "ghost.txt"), cover_image_path=None
        )
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
        document = SimpleNamespace(
            id=9, file_path=str(file_path), cover_image_path=None
        )
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

    @pytest.mark.asyncio
    async def test_delete_removes_cover_file(self, upload_dir, cover_dir):
        """#48：封面文件随主文件一起清理。"""
        file_path = upload_dir / "novel.txt"
        file_path.write_text("content")
        cover_file = cover_dir / "7.png"
        cover_file.write_bytes(b"\x89PNG fake")
        document = SimpleNamespace(
            id=7,
            file_path=str(file_path),
            cover_image_path="covers/7.png",
        )
        db = _FakeAsyncSession(scalar_value=0, rows=[document])

        with patch.object(
            document_service.VectorStoreService, "delete_by_document_id"
        ):
            await delete_document(db, document_id=7)

        assert not file_path.exists()
        assert not cover_file.exists()
        assert db.deleted == [document]

    @pytest.mark.asyncio
    async def test_delete_missing_cover_file_is_ignored(self, upload_dir, cover_dir):
        """#48：封面文件不存在时静默忽略。"""
        file_path = upload_dir / "novel.txt"
        file_path.write_text("content")
        document = SimpleNamespace(
            id=8,
            file_path=str(file_path),
            cover_image_path="covers/ghost.png",
        )
        db = _FakeAsyncSession(scalar_value=0, rows=[document])

        with patch.object(
            document_service.VectorStoreService, "delete_by_document_id"
        ):
            await delete_document(db, document_id=8)

        assert not file_path.exists()
        assert db.deleted == [document]
        assert db.commits == 1
