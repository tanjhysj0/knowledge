"""文档应用服务：上传、列表、删除。"""
import os

import aiofiles
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.document import Document
from app.models.schemas import DocumentResponse, PaginatedDocumentsResponse
from app.services.chunker import TextChunker
from app.services.parser import DocumentParser
from app.services.vector_store import VectorStoreService

settings = get_settings()


class DocumentServiceError(Exception):
    """文档应用服务异常基类。"""


class DocumentNotFoundError(DocumentServiceError):
    """请求的文档不存在。"""


class DocumentParseError(DocumentServiceError):
    """文档解析失败。"""


class DocumentEmptyError(DocumentServiceError):
    """文档无任何可抽取文本。"""


class DocumentChunkError(DocumentServiceError):
    """文档分块失败。"""


async def upload_document(
    *,
    filename: str,
    file_ext: str,
    content: bytes,
    db: AsyncSession,
) -> Document:
    """保存上传文件、解析、分块、写入元数据，并尝试向量存储。"""
    os.makedirs(settings.upload_dir, exist_ok=True)
    file_path = os.path.join(settings.upload_dir, filename)

    async with aiofiles.open(file_path, "wb") as f:
        await f.write(content)

    try:
        text_content = DocumentParser.parse(file_path, file_ext)
    except Exception as exc:
        raise DocumentParseError(str(exc)) from exc

    if not text_content or not text_content.strip():
        raise DocumentEmptyError("Document is empty or contains no extractable text")

    chunker = TextChunker(
        chunk_size=settings.chunk_size,
        overlap=settings.chunk_overlap,
    )
    chunks = chunker.chunk(text_content)
    if not chunks:
        raise DocumentChunkError("Failed to chunk document content")

    document = Document(
        filename=filename,
        file_path=file_path,
        file_type=file_ext,
        size=len(content),
        chunk_count=len(chunks),
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)

    # 向量存储为可选步骤：失败时静默忽略，沿用既有契约（#31 单独修复）。
    try:
        vector_store = VectorStoreService()
        vector_store.insert(
            document_id=document.id,
            chunks=chunks,
            embeddings=[[]] * len(chunks),
        )
    except Exception:
        pass

    return document


async def list_documents(
    db: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 10,
) -> PaginatedDocumentsResponse:
    """按创建时间倒序返回分页的文档列表。"""
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 10
    if page_size > 100:
        page_size = 100

    count_result = await db.execute(select(func.count(Document.id)))
    total = count_result.scalar() or 0

    offset = (page - 1) * page_size
    result = await db.execute(
        select(Document)
        .order_by(Document.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    documents = result.scalars().all()

    total_pages = (total + page_size - 1) // page_size if total > 0 else 0

    return PaginatedDocumentsResponse(
        items=[DocumentResponse.model_validate(doc) for doc in documents],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


async def delete_document(
    db: AsyncSession,
    document_id: int,
) -> None:
    """删除文档元数据、磁盘文件以及向量库条目。"""
    result = await db.execute(select(Document).where(Document.id == document_id))
    document = result.scalar_one_or_none()

    if not document:
        raise DocumentNotFoundError("Document not found")

    # 向量删除为可选步骤：失败时静默忽略，沿用既有契约。
    try:
        vector_store = VectorStoreService()
        vector_store.delete_by_document_id(document_id)
    except Exception:
        pass

    if os.path.exists(document.file_path):
        os.remove(document.file_path)

    await db.delete(document)
    await db.commit()
