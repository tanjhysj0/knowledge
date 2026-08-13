"""#48：上传路由层双文件（正文 + 可选封面）适配测试。"""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api import documents as documents_api
from app.services import documents as document_service


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
            new=AsyncMock(return_value="ok"),
        ) as mock_upload:
            await documents_api.upload_document(file=file, cover=cover, db=object())

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
            new=AsyncMock(return_value="ok"),
        ) as mock_upload:
            await documents_api.upload_document(file=file, cover=None, db=object())

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
                    file=file, cover=cover, db=object()
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
                    file=file, cover=cover, db=object()
                )

        assert exc.value.status_code == 400
        mock_upload.assert_not_called()
