"""文档应用服务：上传、列表、删除。"""
import os
from typing import Optional

import aiofiles
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.document import Document
from app.models.schemas import DocumentResponse, PaginatedDocumentsResponse
from app.services.chunker import TextChunker
from app.services.embedding import get_embedding_provider
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


class DocumentEmbeddingError(DocumentServiceError):
    """生成 embedding 或写入向量库失败。"""


class CoverTypeError(DocumentServiceError):
    """封面扩展名不在白名单内（#48）。"""


class CoverTooLargeError(DocumentServiceError):
    """封面超过 ``cover_max_size`` 限制（#48）。"""


# #48：封面扩展名白名单 → 显式 media type（唯一事实源，api/covers.py 复用）。
ALLOWED_COVER_EXTS = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}


def _validate_cover(
    cover_content: Optional[bytes],
    cover_ext: Optional[str],
) -> None:
    """封面前置校验（#48）：写主文件/落库前拦截非法输入。

    ``cover_content`` 与 ``cover_ext`` 必须成对提供；扩展名不在白名单抛
    :class:`CoverTypeError`，超过 ``cover_max_size`` 抛 :class:`CoverTooLargeError`。
    """
    if cover_content is None and cover_ext is None:
        return
    if cover_content is None or cover_ext is None:
        raise CoverTypeError("cover_content 与 cover_ext 必须成对提供")
    if cover_ext not in ALLOWED_COVER_EXTS:
        raise CoverTypeError(f"Unsupported cover type: {cover_ext}")
    if len(cover_content) > settings.cover_max_size:
        raise CoverTooLargeError("Cover exceeds maximum allowed size")


async def _write_cover(document_id: int, content: bytes, ext: str) -> str:
    """写入封面文件，返回 ``covers/{document_id}.{ext}`` 相对路径（#48）。"""
    os.makedirs(settings.cover_dir, exist_ok=True)
    cover_path = os.path.join(settings.cover_dir, f"{document_id}.{ext}")
    async with aiofiles.open(cover_path, "wb") as f:
        await f.write(content)
    return f"covers/{document_id}.{ext}"


async def upload_document(
    *,
    filename: str,
    file_ext: str,
    content: bytes,
    db: AsyncSession,
    cover_content: Optional[bytes] = None,
    cover_ext: Optional[str] = None,
) -> Document:
    """保存上传文件、解析、分块、写入元数据，并尝试向量存储。

    #48：可选封面（``cover_content`` + ``cover_ext`` 成对提供）在正文
    提交落库后写入 ``cover_dir``，封面非法时在前置校验阶段即抛异常，
    不写主文件、不污染 DB。
    """
    # 封面前置校验：非法输入在写主文件/落库前拦截（#48）。
    _validate_cover(cover_content, cover_ext)

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
    # flush 以生成 document.id（封面文件名 ``covers/{id}.{ext}`` 依赖它）
    await db.flush()

    # #48：封面写入（可选）。封面路径写入后随同一次 commit 持久化。
    if cover_content is not None:
        document.cover_image_path = await _write_cover(
            document.id, cover_content, cover_ext
        )

    await db.commit()
    await db.refresh(document)

    # 向量化：先 embed 再写 Milvus。embedding/向量库异常向上抛转为 5xx（#31）。
    try:
        embedding_provider = get_embedding_provider()
        # embed_texts 是同步 CPU 密集型；放在线程池避免阻塞事件循环。
        import asyncio

        loop = asyncio.get_running_loop()
        embeddings = await loop.run_in_executor(
            None, embedding_provider.embed_texts, chunks
        )
    except Exception as exc:  # noqa: BLE001 — 翻译为业务异常
        raise DocumentEmbeddingError(f"Failed to generate embeddings: {exc}") from exc

    try:
        vector_store = VectorStoreService()
        vector_store.insert(
            document_id=document.id,
            chunks=chunks,
            embeddings=embeddings,
        )
    except Exception as exc:  # noqa: BLE001 — 翻译为业务异常
        raise DocumentEmbeddingError(f"Failed to insert into vector store: {exc}") from exc

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

    # #48：封面文件随主文件一起清理；不存在静默忽略。
    if document.cover_image_path:
        cover_path = os.path.join(
            settings.cover_dir, os.path.basename(document.cover_image_path)
        )
        if os.path.exists(cover_path):
            os.remove(cover_path)

    await db.delete(document)
    await db.commit()
