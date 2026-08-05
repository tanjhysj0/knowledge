"""文档路由层：仅做 HTTP 适配、依赖注入和服务调用。"""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.models.schemas import DocumentResponse, PaginatedDocumentsResponse
from app.services.documents import (
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
    db: AsyncSession = Depends(get_db),
):
    if file.size and file.size > settings.max_file_size:
        raise HTTPException(status_code=400, detail="File too large")

    file_ext = file.filename.split(".")[-1].lower()
    if file_ext not in _ALLOWED_FILE_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {file_ext}")

    content = await file.read()

    try:
        return await document_service.upload_document(
            filename=file.filename,
            file_ext=file_ext,
            content=content,
            db=db,
        )
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
