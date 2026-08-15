"""#66：存量文档辅助索引重建脚本。

用法（backend 目录下）：``python3 -m scripts.rebuild_metadata_indexes [--all] [--id N]``

- 默认重建全部 ``ready`` 且缺失 BM25 索引的小说；
- ``--all`` 强制重建（先清后建，含已有索引的小说）；
- ``--id N`` 只重建指定小说。

依赖 PostgreSQL 服务与 embedding 模型可用（chunks 内容从
``bm25_chunks`` 无法读取时需要重新解析原文；本脚本直接从已上传文件
重新解析分块，与上传管线一致）。
"""
import argparse
import asyncio

from sqlalchemy import select

from app.core.database import get_session_maker, init_db
from app.models.document import Document
from app.services.chunker import TextChunker
from app.services.parser import DocumentParser
from app.services.retrieval.indexing import (
    build_metadata_indexes,
    document_has_metadata_indexes,
)
from app.core.config import get_settings

settings = get_settings()


async def rebuild_one(db, document: Document, force: bool) -> str:
    """重建单本小说的辅助索引，返回结果说明。"""
    if not force and await document_has_metadata_indexes(db, document.id):
        return f"doc {document.id} ({document.title})：已有索引，跳过"
    loop = asyncio.get_running_loop()
    text = await loop.run_in_executor(
        None, DocumentParser.parse, document.file_path, document.file_type
    )
    chunker = TextChunker(chunk_size=settings.chunk_size, overlap=settings.chunk_overlap)
    chunks = await loop.run_in_executor(None, chunker.chunk, text)
    await build_metadata_indexes(db, document.id, text, chunks)
    return f"doc {document.id} ({document.title})：重建完成（{len(chunks)} chunks）"


async def main() -> None:
    parser = argparse.ArgumentParser(description="重建混合检索辅助索引（#66）")
    parser.add_argument("--all", action="store_true", help="强制重建全部（含已有索引的小说）")
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
