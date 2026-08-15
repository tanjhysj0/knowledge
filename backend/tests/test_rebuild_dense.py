"""#72：dense 向量重建脚本单测。

覆盖 ``rebuild_one`` 的幂等跳过 / 重建流程 / force 先清后建 / 空文本与
空分块防护，以及 ``main`` 的 ``--id`` 过滤与单本失败不阻断其余。
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts.rebuild_dense_indexes import main, rebuild_one


def _make_document(doc_id=1, title="测试小说"):
    return SimpleNamespace(
        id=doc_id,
        title=title,
        file_path=f"/tmp/uploads/doc{doc_id}.txt",
        file_type="txt",
        chunk_count=0,
    )


def _patch_deps(*, has_vectors=True, parse_text="第一章 起源\n正文内容", chunks=None):
    """patch 脚本依赖并返回各 mock（vector_store / parser / chunker / embedding）。"""
    vector_store = MagicMock()
    vector_store.has_vectors.return_value = has_vectors
    chunker = MagicMock()
    chunker.chunk.return_value = chunks if chunks is not None else ["c0", "c1"]
    embedding_provider = MagicMock()
    embedding_provider.embed_texts.return_value = [[0.1] * 4, [0.2] * 4]

    patcher_vs = patch(
        "scripts.rebuild_dense_indexes.VectorStoreService",
        return_value=vector_store,
    )
    patcher_parser = patch(
        "scripts.rebuild_dense_indexes.DocumentParser.parse",
        return_value=parse_text,
    )
    patcher_chunker = patch(
        "scripts.rebuild_dense_indexes.TextChunker", return_value=chunker
    )
    patcher_embedding = patch(
        "scripts.rebuild_dense_indexes.get_embedding_provider",
        return_value=embedding_provider,
    )
    return vector_store, chunker, embedding_provider, (
        patcher_vs,
        patcher_parser,
        patcher_chunker,
        patcher_embedding,
    )


class TestRebuildOne:
    @pytest.mark.asyncio
    async def test_skips_when_vectors_exist_without_force(self):
        """已有向量且非 force → 跳过，不解析也不写库。"""
        document = _make_document()
        db = AsyncMock()
        vector_store, chunker, _, patchers = _patch_deps(has_vectors=True)

        with patchers[0], patchers[1], patchers[2], patchers[3]:
            message = await rebuild_one(db, document, force=False)

        assert "已有向量" in message
        vector_store.has_vectors.assert_called_once_with(1)
        vector_store.insert.assert_not_called()
        vector_store.save_document_text.assert_not_called()
        vector_store.delete_by_document_id.assert_not_called()
        chunker.chunk.assert_not_called()
        db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_rebuilds_full_pipeline_when_no_vectors(self):
        """无向量 → 解析 → 全文入库 → 分块 → embedding → 写入向量表。"""
        document = _make_document()
        db = AsyncMock()
        vector_store, _, embedding_provider, patchers = _patch_deps(has_vectors=False)

        with patchers[0], patchers[1], patchers[2], patchers[3]:
            message = await rebuild_one(db, document, force=False)

        assert "重建完成（2 chunks）" in message
        # 非 force 不清空已有数据。
        vector_store.delete_by_document_id.assert_not_called()
        # 全文与向量均写入。
        vector_store.save_document_text.assert_called_once()
        vector_store.insert.assert_called_once()
        # chunk_count 与向量行数保持一致。
        assert document.chunk_count == 2
        db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_force_deletes_before_rebuild(self):
        """force 时即使已有向量也重建：先清空向量与全文再写入。"""
        document = _make_document()
        db = AsyncMock()
        vector_store, _, _, patchers = _patch_deps(has_vectors=True)

        with patchers[0], patchers[1], patchers[2], patchers[3]:
            message = await rebuild_one(db, document, force=True)

        assert "重建完成" in message
        vector_store.delete_by_document_id.assert_called_once_with(1)
        vector_store.save_document_text.assert_called_once()
        vector_store.insert.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_on_empty_parse_text(self):
        """解析结果为空 → 抛异常（由 main 捕获，不阻断其余）。"""
        document = _make_document()
        db = AsyncMock()
        _, _, _, patchers = _patch_deps(has_vectors=False, parse_text="   ")

        with patchers[0], patchers[1], patchers[2], patchers[3]:
            with pytest.raises(ValueError, match="解析结果为空"):
                await rebuild_one(db, document, force=False)

    @pytest.mark.asyncio
    async def test_raises_on_empty_chunks(self):
        """分块结果为空 → 抛异常（由 main 捕获，不阻断其余）。"""
        document = _make_document()
        db = AsyncMock()
        _, _, _, patchers = _patch_deps(has_vectors=False, chunks=[])

        with patchers[0], patchers[1], patchers[2], patchers[3]:
            with pytest.raises(ValueError, match="分块结果为空"):
                await rebuild_one(db, document, force=False)


class TestMain:
    def _session_mock(self, documents):
        """构造 async session mock：execute 返回 documents 列表。"""
        result = MagicMock()
        result.scalars.return_value.all.return_value = documents
        db = AsyncMock()
        db.execute = AsyncMock(return_value=result)
        session_maker = MagicMock()
        session_maker.return_value.__aenter__ = AsyncMock(return_value=db)
        session_maker.return_value.__aexit__ = AsyncMock(return_value=False)
        return session_maker, db

    @pytest.mark.asyncio
    async def test_filters_ready_and_id(self, capsys):
        """查询只包含 ready 小说；--id 时追加单本过滤。"""
        docs = [_make_document(doc_id=1)]
        session_maker, db = self._session_mock(docs)

        with patch("scripts.rebuild_dense_indexes.init_db", new=AsyncMock()), \
                patch("scripts.rebuild_dense_indexes.get_session_maker",
                      return_value=session_maker), \
                patch("scripts.rebuild_dense_indexes.rebuild_one",
                      new=AsyncMock(return_value="ok")), \
                patch("sys.argv", ["rebuild_dense_indexes.py", "--id", "1"]):
            await main()

        stmt = str(db.execute.call_args[0][0])
        assert "documents.status" in stmt
        assert "documents.id" in stmt

    @pytest.mark.asyncio
    async def test_single_failure_does_not_block_others(self, capsys):
        """单本重建失败 → 打印失败信息并继续处理下一本。"""
        docs = [_make_document(doc_id=1), _make_document(doc_id=2, title="第二本")]
        session_maker, _ = self._session_mock(docs)
        rebuild_mock = AsyncMock(
            side_effect=[RuntimeError("embedding down"), "ok"]
        )

        with patch("scripts.rebuild_dense_indexes.init_db", new=AsyncMock()), \
                patch("scripts.rebuild_dense_indexes.get_session_maker",
                      return_value=session_maker), \
                patch("scripts.rebuild_dense_indexes.rebuild_one",
                      new=rebuild_mock), \
                patch("sys.argv", ["rebuild_dense_indexes.py"]):
            await main()

        assert rebuild_mock.await_count == 2
        output = capsys.readouterr().out
        assert "重建失败" in output
        assert "embedding down" in output
