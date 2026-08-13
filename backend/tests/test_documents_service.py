"""Unit tests for the documents application service.

覆盖上传（#63 起仅落库 pending/0）、后台索引任务、列表（ready 过滤）与
删除逻辑；不依赖真实数据库或向量库，使用内存中的假 db Session 和 patch
替换纯 I/O 边界。
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
    _process_document_index,
    delete_document,
    get_document,
    list_documents,
    process_document_index,
    recover_stale_processing_documents,
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
        self.statements: list = []
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
        self.statements.append(sql)
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
    """上传流程：校验、存文件、落库 pending/0（#63，索引移后台）。"""

    @pytest.mark.asyncio
    async def test_success_persists_metadata_pending_without_indexing(self, upload_dir):
        db = _FakeAsyncSession(scalar_value=0)

        with patch.object(
            document_service.DocumentParser, "parse"
        ) as mock_parse, patch.object(
            document_service.TextChunker, "chunk"
        ) as mock_chunk, patch.object(
            document_service, "get_embedding_provider"
        ) as mock_get_provider, patch.object(
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
        assert document.chunk_count == 0
        # #63：上传落库即 pending/0，索引在后台继续。
        assert document.status == "pending"
        assert document.progress == 0
        assert document.error_message is None
        assert db.commits == 1
        assert db.refreshes == [document]
        assert (upload_dir / "hello.txt").read_bytes() == b"hello bytes"
        # 解析/分块/embedding/向量库均不发生在上传路径上
        mock_parse.assert_not_called()
        mock_chunk.assert_not_called()
        mock_get_provider.assert_not_called()
        mock_insert.assert_not_called()


class TestUploadDocumentTitle:
    """#53：上传时的小说名（title）处理。"""

    async def _upload(self, db, *, filename, title=None):
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

    @pytest.mark.asyncio
    async def test_default_lists_only_ready_documents(self):
        """#63：前台书架默认仅返回 ready 小说。"""
        db = _FakeAsyncSession(scalar_value=0, rows=[])

        await list_documents(db)

        # 计数与列表两条语句都带 ``status =`` 过滤（WHERE 子句）。
        assert all("documents.status =" in sql.lower() for sql in db.statements)
        assert len(db.statements) == 2

    @pytest.mark.asyncio
    async def test_all_statuses_skips_ready_filter(self):
        """#63：管理端全量视图不带 ready 过滤。"""
        db = _FakeAsyncSession(scalar_value=0, rows=[])

        await list_documents(db, all_statuses=True)

        # select(Document) 的 SELECT 列表本身含 status 列，但 WHERE 不应有过滤。
        assert all("documents.status =" not in sql.lower() for sql in db.statements)
        assert len(db.statements) == 2


class _ProgressLoggingSession(_FakeAsyncSession):
    """记录每次 commit 时目标文档的 (status, progress) 快照。"""

    def __init__(self, document):
        super().__init__(scalar_value=0, rows=[document])
        self._document = document
        self.progress_log: list = []

    async def commit(self):
        self.commits += 1
        self.progress_log.append((self._document.status, self._document.progress))


class _DisappearingSession(_FakeAsyncSession):
    """前 ``keep_rows`` 次查询返回文档，之后视为已删除返回空。"""

    def __init__(self, document, keep_rows: int):
        super().__init__(scalar_value=0, rows=[document])
        self._document = document
        self._keep_rows = keep_rows
        self._loads = 0

    async def execute(self, statement):
        self._loads += 1
        if self._loads > self._keep_rows:
            return _FakeExecuteResult(rows=[])
        return _FakeExecuteResult(rows=[self._document])


def _make_doc(**kwargs):
    defaults = dict(
        id=5,
        filename="novel.txt",
        file_type="txt",
        size=100,
        file_path="/uploads/novel.txt",
        chunk_count=0,
        title="十日终焉",
        cover_image_path=None,
        status="pending",
        progress=0,
        error_message=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestProcessDocumentIndex:
    """#63：后台索引任务——分阶段进度、ready/failed 终态与删除竞态。"""

    async def _run(
        self,
        db,
        *,
        parse_text="text body",
        chunks=None,
        embed=None,
        embed_error=None,
        insert_error=None,
    ):
        """执行后台索引主流程，patch 全部 I/O 边界。"""
        if chunks is None:
            chunks = ["chunk one", "chunk two"]
        if embed is None:
            embed = [[0.1, 0.2], [0.3, 0.4]]

        with patch.object(
            document_service.DocumentParser, "parse", return_value=parse_text
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
            if embed_error is not None:
                mock_provider.embed_texts = MagicMock(side_effect=embed_error)
            else:
                mock_provider.embed_texts = MagicMock(return_value=embed)
            mock_get_provider.return_value = mock_provider
            if insert_error is not None:
                mock_insert.side_effect = insert_error

            await _process_document_index(db, document_id=5)

        return mock_insert

    @pytest.mark.asyncio
    async def test_success_stages_progress_and_ends_ready(self):
        doc = _make_doc()
        db = _ProgressLoggingSession(doc)
        chunks = ["c1", "c2"]
        embeddings = [[0.1], [0.2]]

        mock_insert = await self._run(db, chunks=chunks, embed=embeddings)

        assert doc.status == "ready"
        assert doc.progress == 100
        assert doc.chunk_count == 2
        assert doc.error_message is None
        # 进度单调推进：processing/5 → 25 → 50 → 75 → 95 → ready/100
        assert [p for _, p in db.progress_log] == [5, 25, 50, 75, 95, 100]
        assert db.progress_log[0][0] == "processing"
        assert db.progress_log[-1][0] == "ready"
        mock_insert.assert_called_once_with(5, chunks, embeddings)

    @pytest.mark.asyncio
    async def test_parse_failure_marks_failed_with_error_message(self):
        doc = _make_doc()
        db = _ProgressLoggingSession(doc)

        with patch.object(
            document_service.DocumentParser, "parse",
            side_effect=ValueError("boom"),
        ), patch.object(
            document_service, "_delete_vectors_quietly"
        ) as mock_cleanup:
            await _process_document_index(db, document_id=5)

        assert doc.status == "failed"
        assert "boom" in doc.error_message
        # 失败态不影响其他小说处理：异常被吞掉转为状态，不外抛
        mock_cleanup.assert_called_once_with(5)

    @pytest.mark.asyncio
    async def test_empty_content_marks_failed(self):
        doc = _make_doc()
        db = _ProgressLoggingSession(doc)

        with patch.object(
            document_service, "_delete_vectors_quietly"
        ):
            await self._run(db, parse_text="   \n\t ")

        assert doc.status == "failed"
        assert "empty" in doc.error_message.lower()

    @pytest.mark.asyncio
    async def test_no_chunks_marks_failed(self):
        doc = _make_doc()
        db = _ProgressLoggingSession(doc)

        with patch.object(
            document_service, "_delete_vectors_quietly"
        ):
            await self._run(db, chunks=[])

        assert doc.status == "failed"
        assert "chunk" in doc.error_message.lower()

    @pytest.mark.asyncio
    async def test_embedding_failure_marks_failed(self):
        doc = _make_doc()
        db = _ProgressLoggingSession(doc)

        with patch.object(
            document_service, "_delete_vectors_quietly"
        ):
            await self._run(db, embed_error=RuntimeError("model down"))

        assert doc.status == "failed"
        assert "model down" in doc.error_message

    @pytest.mark.asyncio
    async def test_vector_insert_failure_marks_failed(self):
        doc = _make_doc()
        db = _ProgressLoggingSession(doc)

        with patch.object(
            document_service, "_delete_vectors_quietly"
        ) as mock_cleanup:
            await self._run(db, insert_error=RuntimeError("milvus down"))

        assert doc.status == "failed"
        assert "milvus down" in doc.error_message
        mock_cleanup.assert_called_once_with(5)

    @pytest.mark.asyncio
    async def test_document_deleted_mid_processing_ignores_result(self):
        """#63：处理中删除——忽略后台任务结果，向量不残留。"""
        doc = _make_doc()
        # 第一次 load 命中后，后续 load 都返回空（模拟并发删除）。
        db = _DisappearingSession(doc, keep_rows=1)

        with patch.object(
            document_service, "_delete_vectors_quietly"
        ) as mock_cleanup:
            await self._run(db)

        # 已删除的小说不被标记 failed，也不走到 ready 终态
        assert doc.status != "failed"
        assert doc.error_message is None
        # 刚写入的向量被清理，不产生孤儿数据
        mock_cleanup.assert_called_once_with(5)

    @pytest.mark.asyncio
    async def test_stale_row_commit_error_is_swallowed(self):
        """并发删除导致 commit 抛 StaleDataError 时不外抛（任务结果被忽略）。"""
        doc = _make_doc()
        # 第一次 load 命中（进入 processing），之后 load 视为已删除。
        db = _DisappearingSession(doc, keep_rows=1)

        # 第一次 commit（进入 processing）成功，之后视作行已被并发删除。
        commit_calls = {"count": 0}

        async def _commit_or_raise():
            from sqlalchemy.orm.exc import StaleDataError

            commit_calls["count"] += 1
            if commit_calls["count"] > 1:
                raise StaleDataError("expected to update 1 row(s); 0 were matched")

        db.commit = _commit_or_raise  # type: ignore[method-assign]

        with patch.object(
            document_service, "_delete_vectors_quietly"
        ) as mock_cleanup:
            await self._run(db)

        # 不外抛即视为通过；已删除的小说不被打上 failed 标记
        assert doc.status != "failed"
        mock_cleanup.assert_called_once_with(5)


class _FakeSessionMaker:
    """假 session maker：``session_maker()`` 返回支持 async with 的会话。"""

    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self._session


class _AsyncCtxSession(_FakeAsyncSession):
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class TestProcessDocumentIndexEntry:
    """#63：后台任务入口——独立 DB 会话的创建与异常兜底。"""

    @pytest.mark.asyncio
    async def test_entry_uses_dedicated_session(self):
        doc = _make_doc()
        session = _AsyncCtxSession(scalar_value=0, rows=[doc])

        with patch.object(
            document_service,
            "get_session_maker",
            return_value=_FakeSessionMaker(session),
        ), patch.object(
            document_service.DocumentParser, "parse", return_value="text body"
        ), patch.object(
            document_service.TextChunker, "chunk", return_value=["c1"]
        ), patch.object(
            document_service, "get_embedding_provider"
        ) as mock_get_provider, patch.object(
            document_service.VectorStoreService, "insert"
        ), patch.object(
            document_service.VectorStoreService, "__init__", return_value=None
        ):
            mock_provider = MagicMock()
            mock_provider.embed_texts = MagicMock(return_value=[[0.1]])
            mock_get_provider.return_value = mock_provider

            await process_document_index(5)

        assert doc.status == "ready"
        assert doc.progress == 100

    @pytest.mark.asyncio
    async def test_entry_swallows_session_failure(self):
        """会话级异常不外抛（后台任务无调用方），仅记录日志。"""

        class _BrokenMaker:
            def __call__(self):
                raise RuntimeError("db down")

        with patch.object(
            document_service,
            "get_session_maker",
            return_value=_BrokenMaker(),
        ):
            # 不外抛即视为通过
            await process_document_index(5)


class _RecoverySession(_FakeAsyncSession):
    """按语句内容返回 stale processing id 或 pending id。"""

    def __init__(self, stale_ids, pending_ids):
        super().__init__()
        self._stale_ids = list(stale_ids)
        self._pending_ids = list(pending_ids)
        self.update_statements: list = []

    async def execute(self, statement):
        sql = str(statement).lower()
        self.statements.append(sql)
        # 编译后的参数里才看得到绑定的状态值（str() 只渲染占位符）。
        params = dict(statement.compile().params)
        values = " ".join(str(v) for v in params.values())
        if "update" in sql:
            self.update_statements.append(sql)
            return _FakeExecuteResult()
        if "processing" in values:
            return _FakeExecuteResult(rows=self._stale_ids)
        if "pending" in values:
            return _FakeExecuteResult(rows=self._pending_ids)
        return _FakeExecuteResult(rows=[])


class TestRecoverStaleProcessingDocuments:
    """#63：启动恢复——processing 重置 pending 并重新入队。"""

    @pytest.mark.asyncio
    async def test_resets_processing_and_returns_pending_ids(self):
        db = _RecoverySession(stale_ids=[1, 2], pending_ids=[3, 4])

        with patch.object(
            document_service, "_delete_vectors_quietly"
        ) as mock_cleanup:
            pending = await recover_stale_processing_documents(db)

        assert pending == [3, 4]
        # 重置语句只覆盖 stale ids，且提交一次
        assert len(db.update_statements) == 1
        assert db.commits == 1
        # 残留 processing 的半成品向量被清理（不留死状态）
        mock_cleanup.assert_any_call(1)
        mock_cleanup.assert_any_call(2)
        assert mock_cleanup.call_count == 2

    @pytest.mark.asyncio
    async def test_no_stale_rows_returns_only_pending(self):
        db = _RecoverySession(stale_ids=[], pending_ids=[7])

        with patch.object(
            document_service, "_delete_vectors_quietly"
        ) as mock_cleanup:
            pending = await recover_stale_processing_documents(db)

        assert pending == [7]
        assert db.update_statements == []
        assert db.commits == 0
        mock_cleanup.assert_not_called()


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
