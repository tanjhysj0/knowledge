"""#72：存量小说 dense 向量重建脚本（pgvector 迁移后数据补齐）。

用法（backend 目录下）：``python3 -m scripts.rebuild_dense_indexes [--all] [--id N]``

- 默认重建全部 ``ready`` 且缺失 dense 向量的小说；
- ``--all`` 强制重建（先清后建，含已有向量的小说）；
- ``--id N`` 只重建指定小说。

每本小说：重新解析原文 → 全文入库（document_texts）→ 分块 →
生成 embedding → 写入 vector_chunks（重嵌入；不从 Milvus 迁移原始
向量）。仅依赖迁移后的单 PG 环境，不依赖 Milvus。
"""
import argparse
import asyncio

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import get_session_maker, init_db
from app.models.document import Document
from app.services.chunker import TextChunker
from app.services.embedding import get_embedding_provider
from app.services.parser import DocumentParser
from app.services.vector_store import VectorStoreService

settings = get_settings()


async def rebuild_one(db, document: Document, force: bool) -> str:
    """重建单本小说的 dense 向量与全文，返回结果说明。

    非 force 且已有向量时直接跳过；force 时先清空向量与全文再重建。
    完成后同步更新 ``chunk_count`` 与向量行数一致。
    """
    loop = asyncio.get_running_loop()
    vector_store = VectorStoreService()

    if not force:
        has_vectors = await loop.run_in_executor(
            None, vector_store.has_vectors, document.id
        )
        if has_vectors:
            return f"doc {document.id} ({document.title})：已有向量，跳过"

    text = await loop.run_in_executor(
        None, DocumentParser.parse, document.file_path, document.file_type
    )
    if not text or not text.strip():
        raise ValueError("解析结果为空")

    chunker = TextChunker(
        chunk_size=settings.chunk_size, overlap=settings.chunk_overlap
    )
    chunks = await loop.run_in_executor(None, chunker.chunk, text)
    if not chunks:
        raise ValueError("分块结果为空")

    embedding_provider = get_embedding_provider()
    embeddings = await loop.run_in_executor(
        None, embedding_provider.embed_texts, chunks
    )

    # force：先清空该小说的向量与全文，避免残留行与重写结果并存。
    if force:
        await loop.run_in_executor(
            None, vector_store.delete_by_document_id, document.id
        )
    await loop.run_in_executor(
        None, vector_store.save_document_text, document.id, text
    )
    await loop.run_in_executor(
        None, vector_store.insert, document.id, chunks, embeddings
    )

    document.chunk_count = len(chunks)
    await db.commit()
    return f"doc {document.id} ({document.title})：重建完成（{len(chunks)} chunks）"


async def main() -> None:
    parser = argparse.ArgumentParser(description="重建 dense 向量与全文（#72）")
    parser.add_argument("--all", action="store_true", help="强制重建全部（含已有向量的小说）")
    parser.add_argument("--id", type=int, default=None, help="只重建指定文档 id")
    args = parser.parse_args()

    await init_db()
    session_maker = get_session_maker()
    async with session_maker() as db:
        stmt = select(Document).where(Document.status == "ready")
        if args.id is not None:
            stmt = stmt.where(Document.id == args.id)
        documents = (await db.execute(stmt)).scalars().all()
        for document in documents:
            try:
                print(await rebuild_one(db, document, force=args.all))
            except Exception as exc:  # noqa: BLE001 — 单本失败不阻断其余
                print(f"doc {document.id} ({document.title})：重建失败：{exc}")


if __name__ == "__main__":
    asyncio.run(main())
