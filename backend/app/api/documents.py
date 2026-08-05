from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
import os
import aiofiles

from app.core.database import get_db
from app.models.document import Document
from app.models.schemas import DocumentResponse
from app.core.config import get_settings

router = APIRouter()
settings = get_settings()


@router.post("/upload", response_model=DocumentResponse)
async def upload_document(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    if file.size and file.size > settings.max_file_size:
        raise HTTPException(status_code=400, detail="File too large")

    allowed_types = {"txt", "md", "pdf", "docx"}
    file_ext = file.filename.split(".")[-1].lower()
    if file_ext not in allowed_types:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {file_ext}")

    os.makedirs(settings.upload_dir, exist_ok=True)
    file_path = os.path.join(settings.upload_dir, file.filename)

    async with aiofiles.open(file_path, "wb") as f:
        content = await file.read()
        await f.write(content)

    document = Document(
        filename=file.filename,
        file_path=file_path,
        file_type=file_ext,
        size=len(content),
        chunk_count=0,
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)

    return document


@router.get("", response_model=List[DocumentResponse])
async def list_documents(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Document).order_by(Document.created_at.desc()))
    documents = result.scalars().all()
    return documents


@router.delete("/{document_id}")
async def delete_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Document).where(Document.id == document_id))
    document = result.scalar_one_or_none()

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    if os.path.exists(document.file_path):
        os.remove(document.file_path)

    await db.delete(document)
    await db.commit()

    return {"message": "Document deleted"}
