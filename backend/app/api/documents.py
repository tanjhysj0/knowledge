"""文档路由层：仅做 HTTP 适配、依赖注入和服务调用。"""
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.models.schemas import DocumentResponse, PaginatedDocumentsResponse
from app.services.documents import (
    ALLOWED_COVER_EXTS,
    CoverTooLargeError,
    CoverTypeError,
    DocumentChunkError,
    DocumentEmptyError,
    DocumentNotFoundError,
    DocumentParseError,
)
from app.services import documents as document_service

router = APIRouter()
settings = get_settings()

_ALLOWED_FILE_TYPES = {"txt", "md", "pdf", "docx"}


@router.post("/upload", response_model=DocumentResponse)
async def upload_document(
    file: UploadFile = File(...),
    cover: Optional[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db),
):
    """上传小说正文（必填）与封面（可选，#48）。

    校验顺序（#48）：先正文 type/size → 读正文 → 再封面 ext/size → 读封面。
    封面非法返回 400，不落库、不写文件。
    """
    if file.size and file.size > settings.max_file_size:
        raise HTTPException(status_code=400, detail="File too large")

    file_ext = file.filename.split(".")[-1].lower()
    if file_ext not in _ALLOWED_FILE_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {file_ext}")

    content = await file.read()

    # 封面校验：扩展名白名单 → size → 读内容后按实际长度复核（#48）。
    cover_content: Optional[bytes] = None
    cover_ext: Optional[str] = None
    if cover is not None:
        cover_ext = cover.filename.rsplit(".", 1)[-1].lower() if "." in cover.filename else ""
        if cover_ext not in ALLOWED_COVER_EXTS:
            raise HTTPException(
                status_code=400, detail=f"Unsupported cover type: {cover_ext}"
            )
        if cover.size and cover.size > settings.cover_max_size:
            raise HTTPException(status_code=400, detail="Cover too large")
        cover_content = await cover.read()
        if len(cover_content) > settings.cover_max_size:
            raise HTTPException(status_code=400, detail="Cover too large")

    try:
        return await document_service.upload_document(
            filename=file.filename,
            file_ext=file_ext,
            content=content,
            db=db,
            cover_content=cover_content,
            cover_ext=cover_ext,
        )
    except CoverTypeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CoverTooLargeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DocumentParseError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to parse document: {exc}",
        ) from exc
    except DocumentEmptyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DocumentChunkError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("", response_model=PaginatedDocumentsResponse)
async def list_documents(
    page: int = 1,
    page_size: int = 10,
    db: AsyncSession = Depends(get_db),
):
    return await document_service.list_documents(
        db=db,
        page=page,
        page_size=page_size,
    )


@router.delete("/{document_id}")
async def delete_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
):
    try:
        await document_service.delete_document(db=db, document_id=document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"message": "Document deleted"}
