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
from app.services.parser import DocumentParser
from app.services.chunker import TextChunker
from app.services.embedding import EmbeddingService
from app.services.vector_store import VectorStoreService

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

    # Parse document content
    try:
        text_content = DocumentParser.parse(file_path, file_ext)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse document: {str(e)}")

    if not text_content or not text_content.strip():
        raise HTTPException(status_code=400, detail="Document is empty or contains no extractable text")

    # Chunk text
    chunker = TextChunker(chunk_size=settings.chunk_size, overlap=settings.chunk_overlap)
    chunks = chunker.chunk(text_content)

    if not chunks:
        raise HTTPException(status_code=400, detail="Failed to chunk document content")

    # Create document record first
    document = Document(
        filename=file.filename,
        file_path=file_path,
        file_type=file_ext,
        size=len(content),
        chunk_count=len(chunks),
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)

    # Generate embeddings and store in Milvus
    try:
        embedding_service = EmbeddingService()
        embeddings = await embedding_service.embed_texts(chunks)

        vector_store = VectorStoreService()
        vector_store.insert(
            document_id=document.id,
            chunks=chunks,
            embeddings=embeddings,
        )
    except Exception as e:
        # Rollback document if vector storage fails
        await db.delete(document)
        await db.commit()
        raise HTTPException(status_code=500, detail=f"Failed to process document: {str(e)}")

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

    # Delete from Milvus first
    try:
        vector_store = VectorStoreService()
        vector_store.delete_by_document_id(document_id)
    except Exception:
        pass  # Continue with file and DB deletion even if Milvus fails

    if os.path.exists(document.file_path):
        os.remove(document.file_path)

    await db.delete(document)
    await db.commit()

    return {"message": "Document deleted"}
