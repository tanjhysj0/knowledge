"""#48：上传路由层双文件（正文 + 可选封面）适配测试。

#63：上传成功后索引处理经 ``BackgroundTasks`` 入队，响应只等落库。
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api import documents as documents_api
from app.services import documents as document_service


def _fake_background_tasks():
    """#63：假 BackgroundTasks，记录 add_task 调用。"""
    return SimpleNamespace(add_task=MagicMock())


def _uploaded_document(document_id=42):
    """#63：路由取 ``document.id`` 入队，返回值须携带 id。"""
    return SimpleNamespace(id=document_id)


class _FakeUploadFile:
    """Stand-in for starlette ``UploadFile``。"""

    def __init__(self, filename, content=b"", size=None):
        self.filename = filename
        self._content = content
        self.size = size if size is not None else len(content)

    async def read(self):
        return self._content


class TestUploadRouteCoverForwarding:
    """路由层：multipart 解析后向 service 转发双文件字段。"""

    @pytest.mark.asyncio
    async def test_forwards_cover_content_and_ext_to_service(self):
        file = _FakeUploadFile(filename="novel.txt", content=b"novel body")
        cover = _FakeUploadFile(filename="book.png", content=b"\x89PNG fake")

        with patch.object(
            document_service,
            "upload_document",
            new=AsyncMock(return_value=_uploaded_document()),
        ) as mock_upload:
            await documents_api.upload_document(
                file=file, cover=cover, db=object(),
                background_tasks=_fake_background_tasks(),
            )

        kwargs = mock_upload.await_args.kwargs
        assert kwargs["cover_content"] == b"\x89PNG fake"
        assert kwargs["cover_ext"] == "png"
        assert kwargs["content"] == b"novel body"

    @pytest.mark.asyncio
    async def test_omits_cover_fields_when_no_cover_uploaded(self):
        file = _FakeUploadFile(filename="novel.txt", content=b"novel body")

        with patch.object(
            document_service,
            "upload_document",
            new=AsyncMock(return_value=_uploaded_document()),
        ) as mock_upload:
            await documents_api.upload_document(
                file=file, cover=None, db=object(),
                background_tasks=_fake_background_tasks(),
            )

        kwargs = mock_upload.await_args.kwargs
        assert kwargs["cover_content"] is None
        assert kwargs["cover_ext"] is None

    @pytest.mark.asyncio
    async def test_rejects_invalid_cover_type_with_400(self):
        file = _FakeUploadFile(filename="novel.txt", content=b"novel body")
        cover = _FakeUploadFile(filename="book.gif", content=b"GIF89a")

        with patch.object(
            document_service, "upload_document", new=AsyncMock()
        ) as mock_upload:
            with pytest.raises(HTTPException) as exc:
                await documents_api.upload_document(
                    file=file, cover=cover, db=object(),
                    background_tasks=_fake_background_tasks(),
                )

        assert exc.value.status_code == 400
        assert "gif" in str(exc.value.detail)
        mock_upload.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_oversized_cover_with_400(self, monkeypatch):
        monkeypatch.setattr(documents_api.settings, "cover_max_size", 10)
        file = _FakeUploadFile(filename="novel.txt", content=b"novel body")
        cover = _FakeUploadFile(filename="book.png", content=b"x" * 11)

        with patch.object(
            document_service, "upload_document", new=AsyncMock()
        ) as mock_upload:
            with pytest.raises(HTTPException) as exc:
                await documents_api.upload_document(
                    file=file, cover=cover, db=object(),
                    background_tasks=_fake_background_tasks(),
                )

        assert exc.value.status_code == 400
        mock_upload.assert_not_called()


class TestUploadRouteBackgroundIndexing:
    """#63：上传成功后经 BackgroundTasks 入队索引任务。"""

    @pytest.mark.asyncio
    async def test_enqueues_index_task_with_document_id(self):
        file = _FakeUploadFile(filename="novel.txt", content=b"novel body")
        background_tasks = _fake_background_tasks()

        with patch.object(
            document_service,
            "upload_document",
            new=AsyncMock(return_value=_uploaded_document(42)),
        ):
            result = await documents_api.upload_document(
                file=file, cover=None, db=object(),
                background_tasks=background_tasks,
            )

        background_tasks.add_task.assert_called_once_with(
            document_service.process_document_index, 42
        )
        assert result.id == 42

    @pytest.mark.asyncio
    async def test_does_not_enqueue_when_cover_invalid(self):
        file = _FakeUploadFile(filename="novel.txt", content=b"novel body")
        cover = _FakeUploadFile(filename="book.gif", content=b"GIF89a")
        background_tasks = _fake_background_tasks()

        with patch.object(
            document_service, "upload_document", new=AsyncMock()
        ):
            with pytest.raises(HTTPException):
                await documents_api.upload_document(
                    file=file, cover=cover, db=object(),
                    background_tasks=background_tasks,
                )

        background_tasks.add_task.assert_not_called()


class TestListRouteStatusFilter:
    """#63：``all_statuses`` 查询参数透传 service。"""

    @pytest.mark.asyncio
    async def test_defaults_to_ready_only(self):
        with patch.object(
            document_service, "list_documents", new=AsyncMock(return_value="ok")
        ) as mock_list:
            await documents_api.list_documents(db=object())

        kwargs = mock_list.await_args.kwargs
        assert kwargs["all_statuses"] is False

    @pytest.mark.asyncio
    async def test_all_statuses_true_forwarded(self):
        with patch.object(
            document_service, "list_documents", new=AsyncMock(return_value="ok")
        ) as mock_list:
            await documents_api.list_documents(db=object(), all_statuses=True)

        kwargs = mock_list.await_args.kwargs
        assert kwargs["all_statuses"] is True
