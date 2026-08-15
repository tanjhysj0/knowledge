"""文档应用服务：上传、后台索引、列表、删除。

#63：上传与索引分离——上传只做校验/存文件/落库（``pending``/0）并立即返回；
解析 → 分块 → embedding → 向量库写入由 :func:`process_document_index`
在后台任务中完成，逐阶段把进度/状态写回小说表。
"""
import asyncio
import logging
import os
from typing import List, Optional

import aiofiles
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session_maker
from app.models.document import Document
from app.models.schemas import DocumentResponse, PaginatedDocumentsResponse
from app.services.chunker import TextChunker
from app.services.embedding import get_embedding_provider
from app.services.parser import DocumentParser
from app.services.vector_store import VectorStoreService

settings = get_settings()
logger = logging.getLogger(__name__)


class DocumentServiceError(Exception):
    """文档应用服务异常基类。"""


class DocumentNotFoundError(DocumentServiceError):
    """请求的文档不存在。"""


class DocumentNotFailedError(DocumentServiceError):
    """重试索引目标不是 failed 状态（#65）。"""


class DocumentParseError(DocumentServiceError):
    """文档解析失败。"""


class DocumentEmptyError(DocumentServiceError):
    """文档无任何可抽取文本。"""


class DocumentChunkError(DocumentServiceError):
    """文档分块失败。"""


class DocumentEmbeddingError(DocumentServiceError):
    """生成 embedding 或写入向量存储失败。"""


class DocumentStoreError(DocumentServiceError):
    """解析后全文写入 PG 存储失败（#71）。"""


class CoverTypeError(DocumentServiceError):
    """封面扩展名不在白名单内（#48）。"""


class CoverTooLargeError(DocumentServiceError):
    """封面超过 ``cover_max_size`` 限制（#48）。"""


class DocumentTitleError(DocumentServiceError):
    """小说名为空或非法（#53）。"""


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
    title: Optional[str] = None,
) -> Document:
    """保存上传文件并写入元数据，索引处理交由后台任务（#63）。

    只做校验、存文件、落库三步：落库即 ``status=pending``、``progress=0``
    并立即返回（上传响应秒级）。解析 → 分块 → embedding → 向量库写入由
    :func:`process_document_index` 在后台完成。

    #48：可选封面（``cover_content`` + ``cover_ext`` 成对提供）在正文
    提交落库后写入 ``cover_dir``，封面非法时在前置校验阶段即抛异常，
    不写主文件、不污染 DB。

    #53：``title`` 为小说名；缺省或空白时回退文件名去扩展名。
    """
    # 封面前置校验：非法输入在写主文件/落库前拦截（#48）。
    _validate_cover(cover_content, cover_ext)

    os.makedirs(settings.upload_dir, exist_ok=True)
    file_path = os.path.join(settings.upload_dir, filename)

    async with aiofiles.open(file_path, "wb") as f:
        await f.write(content)

    document = Document(
        filename=filename,
        # #53：小说名缺省回退文件名去扩展名。
        title=title.strip() if title and title.strip() else os.path.splitext(filename)[0],
        file_path=file_path,
        file_type=file_ext,
        size=len(content),
        chunk_count=0,
        # #63：上传落库即 pending/0，索引在后台继续。
        status="pending",
        progress=0,
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

    return document


# ---------------------------------------------------------------------------
# #63：后台索引处理（解析 → 分块 → embedding → 向量库写入）
# ---------------------------------------------------------------------------

# 各阶段的进度落点（0-100）。
PROGRESS_START = 5      # 进入处理（status=processing）
PROGRESS_PARSED = 25    # 解析完成
PROGRESS_CHUNKED = 50   # 分块完成
PROGRESS_EMBEDDED = 75  # embedding 完成
PROGRESS_INDEXED = 95   # 向量写入完成


async def _load_document(
    db: AsyncSession, document_id: int
) -> Optional[Document]:
    """按 id 取文档；不存在返回 ``None``（处理中删除的检测点）。"""
    result = await db.execute(select(Document).where(Document.id == document_id))
    return result.scalar_one_or_none()


async def _update_progress(
    db: AsyncSession,
    document: Document,
    *,
    status: str = "processing",
    progress: int,
) -> None:
    """把当前阶段的状态/进度写回小说表并提交。"""
    document.status = status
    document.progress = progress
    await db.commit()


def _delete_vectors_quietly(document_id: int) -> None:
    """尽力清理该小说的向量（失败/删除竞态的残留清理，失败不阻塞主流程）。

    显式传 ``dim`` 避免在启动恢复路径上首次加载 embedding 模型。
    """
    try:
        VectorStoreService(dim=settings.embedding_dim).delete_by_document_id(
            document_id
        )
    except Exception as exc:  # noqa: BLE001 — 清理失败可容忍
        logger.warning("vector cleanup for document %s failed: %s", document_id, exc)


async def _clear_metadata_indexes_quietly(db: AsyncSession, document_id: int) -> None:
    """#66：尽力清理小说的辅助索引（删除/重试路径，失败不阻塞主流程）。"""
    try:
        from app.services.retrieval.indexing import clear_metadata_indexes

        await clear_metadata_indexes(db, document_id)
    except Exception as exc:  # noqa: BLE001 — 清理失败可容忍
        logger.warning("metadata index cleanup for document %s failed: %s", document_id, exc)


async def _mark_failed(db: AsyncSession, document_id: int, error: Exception) -> None:
    """把小说标记为 ``failed`` 并记录错误信息；已被删除则忽略任务结果。

    失败可能发生在向量写入中途：先尽力清理残留向量，避免半成品参与检索。
    """
    _delete_vectors_quietly(document_id)
    await _clear_metadata_indexes_quietly(db, document_id)
    document = await _load_document(db, document_id)
    if document is None:
        return
    document.status = "failed"
    document.error_message = str(error)
    await db.commit()


async def _parse_text(file_path: str, file_ext: str) -> str:
    """解析正文；异常翻译为 :class:`DocumentParseError`。"""
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            None, DocumentParser.parse, file_path, file_ext
        )
    except Exception as exc:
        raise DocumentParseError(str(exc)) from exc


async def _chunk_text(text_content: str) -> List[str]:
    """按配置分块；同步 CPU 密集调用丢到线程池。"""
    chunker = TextChunker(
        chunk_size=settings.chunk_size,
        overlap=settings.chunk_overlap,
    )
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, chunker.chunk, text_content)


async def _embed_chunks(chunks: List[str]) -> List[List[float]]:
    """生成 chunks 的 embedding；异常翻译为 :class:`DocumentEmbeddingError`。"""
    embedding_provider = get_embedding_provider()
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            None, embedding_provider.embed_texts, chunks
        )
    except Exception as exc:
        raise DocumentEmbeddingError(f"Failed to generate embeddings: {exc}") from exc


async def _insert_vectors(
    document_id: int, chunks: List[str], embeddings: List[List[float]]
) -> None:
    """写入 PG ``vector_chunks``（#71）；异常翻译为 :class:`DocumentEmbeddingError`。"""
    vector_store = VectorStoreService()
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(
            None, vector_store.insert, document_id, chunks, embeddings
        )
    except Exception as exc:
        raise DocumentEmbeddingError(
            f"Failed to insert into vector store: {exc}"
        ) from exc


async def _save_document_text(document_id: int, text_content: str) -> None:
    """解析后的全文写入 PG ``document_texts``（#71）；异常翻译为
    :class:`DocumentStoreError`。"""
    vector_store = VectorStoreService()
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(
            None, vector_store.save_document_text, document_id, text_content
        )
    except Exception as exc:
        raise DocumentStoreError(f"Failed to store document text: {exc}") from exc


async def _process_document_index(db: AsyncSession, document_id: int) -> None:
    """后台索引主流程：解析 → 分块 → embedding → 向量写入，逐阶段写回进度。

    成功：``ready``/100；失败：``failed`` + ``error_message``。小说在
    处理中被删除时静默退出（忽略任务结果），刚写入的向量一并清理，
    不产生孤儿数据（#63）。
    """
    document = await _load_document(db, document_id)
    if document is None:
        # 上传后、处理前已被删除：无任务结果可写。
        return

    await _update_progress(
        db, document, status="processing", progress=PROGRESS_START
    )

    try:
        text_content = await _parse_text(document.file_path, document.file_type)
        if not text_content or not text_content.strip():
            raise DocumentEmptyError(
                "Document is empty or contains no extractable text"
            )
        # #71：解析后的全文入库（document_texts），可从库中完整还原文本。
        await _save_document_text(document_id, text_content)
        await _update_progress(db, document, progress=PROGRESS_PARSED)

        chunks = await _chunk_text(text_content)
        if not chunks:
            raise DocumentChunkError("Failed to chunk document content")
        document.chunk_count = len(chunks)
        await _update_progress(db, document, progress=PROGRESS_CHUNKED)

        embeddings = await _embed_chunks(chunks)
        await _update_progress(db, document, progress=PROGRESS_EMBEDDED)

        await _insert_vectors(document_id, chunks, embeddings)
        await _update_progress(db, document, progress=PROGRESS_INDEXED)

        # #66：混合检索辅助索引（BM25/章节/实体/事件）。构建失败不阻断
        # 上传（PRD 兼容性要求）：仅记录，事后可用重建脚本补齐。
        try:
            from app.services.retrieval.indexing import build_metadata_indexes

            await build_metadata_indexes(db, document_id, text_content, chunks)
        except Exception as exc:  # noqa: BLE001 — 辅助索引失败不阻断 ready
            logger.warning(
                "metadata indexes for document %s failed: %s", document_id, exc
            )
    except Exception as exc:  # noqa: BLE001 — 任何阶段失败都转为 failed 状态
        await _mark_failed(db, document_id, exc)
        return

    # 删除竞态补偿：向量写入后复核小说仍存在；已被删则清理刚写入的
    # 向量并静默退出（delete 端点已删元数据与文件）。
    if await _load_document(db, document_id) is None:
        _delete_vectors_quietly(document_id)
        return

    await _update_progress(db, document, status="ready", progress=100)


async def process_document_index(document_id: int) -> None:
    """后台任务入口：用独立 DB 会话完成索引处理（#63）。

    会话级异常不向外传播（后台任务无调用方）：记录日志，残留的
    ``processing`` 状态由启动恢复重置并重新入队。
    """
    session_maker = get_session_maker()
    try:
        async with session_maker() as db:
            await _process_document_index(db, document_id)
    except Exception as exc:  # noqa: BLE001 — 后台任务兜底
        logger.exception("document %s background indexing failed: %s", document_id, exc)


async def requeue_document_index(
    db: AsyncSession, document_id: int
) -> Document:
    """重试索引（#65）：failed 小说重置为 pending，由调用方重新入队。

    仅 ``failed`` 状态可重试，其余状态抛 :class:`DocumentNotFailedError`；
    重置前尽力清理失败残留的向量（如写入中途的半成品条目），并清空
    error_message 与 chunk_count，与首次上传共用同一后台处理链路。
    """
    document = await _load_document(db, document_id)
    if document is None:
        raise DocumentNotFoundError("Document not found")
    if document.status != "failed":
        raise DocumentNotFailedError(
            f"Document is {document.status}, only failed documents can be re-indexed"
        )

    # 重试前清除失败残留：半写入的向量条目一并清理，避免重复数据（#65）。
    _delete_vectors_quietly(document_id)
    # #66：辅助索引（BM25/章节/实体/事件）随失败残留一并清理，重建时重写。
    await _clear_metadata_indexes_quietly(db, document_id)

    document.status = "pending"
    document.progress = 0
    document.error_message = None
    document.chunk_count = 0
    await db.commit()
    await db.refresh(document)

    return document


async def recover_stale_processing_documents(db: AsyncSession) -> List[int]:
    """启动恢复（#63）：``processing`` 重置为 ``pending``，返回待重新入队的 id。

    服务重启后残留的 ``processing`` 不留死状态：重置回 ``pending`` 并清掉
    可能写了一半的向量，由调用方重新入队处理；返回值同时包含全部
    ``pending`` id（覆盖重启前上传后未及处理的新记录）。
    """
    stale_result = await db.execute(
        select(Document.id).where(Document.status == "processing")
    )
    stale_ids = list(stale_result.scalars().all())
    if stale_ids:
        await db.execute(
            update(Document)
            .where(Document.id.in_(stale_ids))
            .values(status="pending", progress=0)
        )
        await db.commit()
        for doc_id in stale_ids:
            _delete_vectors_quietly(doc_id)
            await _clear_metadata_indexes_quietly(db, doc_id)

    pending_result = await db.execute(
        select(Document.id).where(Document.status == "pending")
    )
    return list(pending_result.scalars().all())


async def update_document(
    db: AsyncSession,
    document_id: int,
    *,
    title: Optional[str] = None,
    cover_content: Optional[bytes] = None,
    cover_ext: Optional[str] = None,
) -> Document:
    """编辑小说（#53）：仅支持改小说名与换封面，正文不可换。

    ``title`` 提供时更新（strip 后为空抛 :class:`DocumentTitleError`）；
    ``cover_content`` 提供时写入新封面并清理旧封面文件。两者均未提供时
    抛 :class:`DocumentTitleError` 视为空编辑。
    """
    result = await db.execute(select(Document).where(Document.id == document_id))
    document = result.scalar_one_or_none()
    if not document:
        raise DocumentNotFoundError("Document not found")

    _validate_cover(cover_content, cover_ext)

    if title is None and cover_content is None:
        raise DocumentTitleError("至少提供小说名或封面中的一个字段")

    if title is not None:
        trimmed = title.strip()
        if not trimmed:
            raise DocumentTitleError("小说名不能为空")
        if len(trimmed) > 255:
            raise DocumentTitleError("小说名过长（最多 255 字符）")
        document.title = trimmed

    # #53：换封面——先写新文件，DB 提交成功后再清理旧文件，避免 commit
    # 失败时数据库仍指向已被删除的旧封面。
    replaced_cover = False
    if cover_content is not None:
        new_path = await _write_cover(document.id, cover_content, cover_ext)
        old_path = document.cover_image_path
        document.cover_image_path = new_path
        replaced_cover = old_path is not None and old_path != new_path

    await db.commit()
    await db.refresh(document)

    if replaced_cover:
        old_full = os.path.join(settings.cover_dir, os.path.basename(old_path))
        if os.path.exists(old_full):
            try:
                os.remove(old_full)
            except OSError:
                pass  # 旧封面残留可容忍，不影响数据一致性

    return document


async def get_document(db: AsyncSession, document_id: int) -> Document:
    """单文档详情：管理端编辑页按 id 拉取预填数据。

    不存在时抛 :class:`DocumentNotFoundError`。
    """
    result = await db.execute(select(Document).where(Document.id == document_id))
    document = result.scalar_one_or_none()
    if not document:
        raise DocumentNotFoundError("Document not found")
    return document


async def list_documents(
    db: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 10,
    all_statuses: bool = False,
) -> PaginatedDocumentsResponse:
    """按创建时间倒序返回分页的文档列表。

    #63：默认仅返回 ``ready`` 小说（前台书架）；``all_statuses=True`` 时
    返回全量视图（管理端）。
    """
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 10
    if page_size > 100:
        page_size = 100

    # 前台书架只看到 ready 小说（#63）。
    ready_only = not all_statuses

    count_stmt = select(func.count(Document.id))
    if ready_only:
        count_stmt = count_stmt.where(Document.status == "ready")
    count_result = await db.execute(count_stmt)
    total = count_result.scalar() or 0

    offset = (page - 1) * page_size
    list_stmt = select(Document).order_by(Document.created_at.desc())
    if ready_only:
        list_stmt = list_stmt.where(Document.status == "ready")
    result = await db.execute(
        list_stmt.offset(offset).limit(page_size)
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

    # #66：辅助索引（BM25/章节/实体/事件）随删除清理；失败静默忽略。
    try:
        from app.services.retrieval.indexing import clear_metadata_indexes

        await clear_metadata_indexes(db, document_id)
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
