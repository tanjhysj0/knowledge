"""#80：图谱抽取/构建/查询/写入与文档生命周期钩子单测（fake session）。"""
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.graph import GraphEntity, GraphRelation
from app.services import documents as document_service
from app.services.graph import (
    _dedupe_relations,
    _parse_triple_result,
    build_entity_rows,
    build_graph_indexes,
    clear_graph_indexes,
    create_triple,
    document_has_graph_indexes,
    extract_graph_triples,
    get_neighbors,
    list_triples,
)
from app.services.mock_llm import MOCK_EXTRACT_TRIPLES_RESPONSE, MockLLMProvider


def _batch():
    return [(0, "内容零"), (1, "内容一")]


def _rel(subject, relation, obj, chunk=0):
    return GraphRelation(
        document_id=1,
        subject=subject,
        relation=relation,
        object=obj,
        chunk_index=chunk,
        content=f"c{chunk}",
    )


class TestParseTripleResult:
    def test_parses_triples(self):
        raw = (
            '{"triples": [{"subject": "张三", "relation": "是", '
            '"object": "主角", "chunk": 1}]}'
        )
        relations = _parse_triple_result(1, raw, _batch())
        assert len(relations) == 1
        rel = relations[0]
        assert (rel.document_id, rel.subject, rel.relation, rel.object) == (
            1, "张三", "是", "主角",
        )
        assert rel.chunk_index == 1
        # 源文本块引用：chunk 1 的原文内容。
        assert rel.content == "内容一"

    def test_tolerates_wrapped_json(self):
        raw = (
            '好的：```json\n{"triples": [{"subject": "张三", "relation": "是", '
            '"object": "主角", "chunk": 0}]}\n```'
        )
        relations = _parse_triple_result(1, raw, _batch())
        assert len(relations) == 1

    def test_invalid_payloads_return_empty(self):
        assert _parse_triple_result(1, "", _batch()) == []
        assert _parse_triple_result(1, "not json", _batch()) == []
        assert _parse_triple_result(1, '{"triples": "x"}', _batch()) == []
        # 缺字段 / chunk 非 int / 字段为空串均被跳过
        assert _parse_triple_result(1, '{"triples": [{"subject": "x"}]}', _batch()) == []
        assert (
            _parse_triple_result(
                1,
                '{"triples": [{"subject": "x", "relation": "r", "object": "y", "chunk": "1"}]}',
                _batch(),
            )
            == []
        )
        assert (
            _parse_triple_result(
                1,
                '{"triples": [{"subject": " ", "relation": "r", "object": "y", "chunk": 0}]}',
                _batch(),
            )
            == []
        )


class TestExtractGraphTriples:
    @pytest.mark.asyncio
    async def test_llm_not_configured_skips(self):
        # is_llm_configured 在函数内延迟 import，patch 目标在 app.services.llm。
        with patch("app.services.llm.is_llm_configured", return_value=(False, None)):
            assert await extract_graph_triples(1, ["c1"]) == []

    @pytest.mark.asyncio
    async def test_injected_llm_extracts_triples(self):
        llm = MagicMock()
        llm.chat = AsyncMock(
            return_value=(
                '{"triples": [{"subject": "张三", "relation": "是", '
                '"object": "主角", "chunk": 0}]}'
            )
        )
        relations = await extract_graph_triples(1, ["张三登场"], llm=llm)
        assert len(relations) == 1
        assert relations[0].subject == "张三"
        assert relations[0].content == "张三登场"

    @pytest.mark.asyncio
    async def test_llm_failure_skips_batch(self):
        """抽取失败静默降级：返回空列表，不抛异常。"""
        llm = MagicMock()
        llm.chat = AsyncMock(side_effect=RuntimeError("llm down"))
        assert await extract_graph_triples(1, ["c1"], llm=llm) == []

    @pytest.mark.asyncio
    async def test_mock_provider_returns_fixed_triples(self):
        """#80：MockLLMProvider 对抽取 prompt 返回确定性三元组（E2E 可断言）。"""
        llm = MockLLMProvider()
        relations = await extract_graph_triples(1, ["张三与李四大战"], llm=llm)
        # 固定输出：张三-是-主角 与 李四-击败-张三。
        assert {(r.subject, r.relation, r.object) for r in relations} == {
            ("张三", "是", "主角"),
            ("李四", "击败", "张三"),
        }


class TestBuildEntityRows:
    def test_dedupes_subject_and_object(self):
        relations = [
            _rel("张三", "是", "主角", chunk=0),
            _rel("李四", "击败", "张三", chunk=1),
        ]
        entities = build_entity_rows(1, relations)
        assert {e.name for e in entities} == {"张三", "主角", "李四"}
        # 实体 chunk 引用取首次出现的三元组。
        zhang = next(e for e in entities if e.name == "张三")
        assert zhang.chunk_index == 0
        assert zhang.document_id == 1

    def test_empty_relations_empty_entities(self):
        assert build_entity_rows(1, []) == []


class TestDedupeRelations:
    def test_removes_duplicate_triples(self):
        # 多批 mock 输出可能重复，去重后只保留一条。
        result = _dedupe_relations([_rel("张三", "是", "主角"), _rel("张三", "是", "主角")])
        assert len(result) == 1

    def test_keeps_distinct_triples(self):
        result = _dedupe_relations([_rel("张三", "是", "主角"), _rel("李四", "击败", "张三")])
        assert len(result) == 2


class _FakeSession:
    """记录 add/execute/commit 的 AsyncSession 替身。"""

    def __init__(self, rows=None, predicate=None):
        self.rows = rows or []
        self.predicate = predicate
        self.added = []
        self.commits = 0
        self.statements = []

    def add(self, obj):
        self.added.append(obj)

    def add_all(self, rows):
        self.added.extend(rows)

    async def flush(self):
        pass

    async def delete(self, obj):
        self.deleted = getattr(self, "deleted", [])
        self.deleted.append(obj)

    async def execute(self, statement):
        self.statements.append(statement)
        rows = [r for r in self.rows if self.predicate is None or self.predicate(r)]
        return SimpleNamespace(
            scalar_one_or_none=lambda: rows[0] if rows else None,
            scalars=lambda: SimpleNamespace(all=lambda: rows),
        )

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        pass


class TestBuildGraphIndexes:
    @pytest.mark.asyncio
    async def test_builds_entities_and_relations(self):
        db = _FakeSession()
        with patch(
            "app.services.graph.extract_graph_triples",
            new=AsyncMock(return_value=[_rel("张三", "是", "主角")]),
        ):
            await build_graph_indexes(db, 1, ["c0"])

        assert db.commits == 1
        entities = [o for o in db.added if isinstance(o, GraphEntity)]
        relations = [o for o in db.added if isinstance(o, GraphRelation)]
        assert {e.name for e in entities} == {"张三", "主角"}
        assert len(relations) == 1

    @pytest.mark.asyncio
    async def test_clears_before_build(self):
        """幂等：构建前先清后建，重索引不会产生重复图数据。"""
        db = _FakeSession()
        with patch(
            "app.services.graph.extract_graph_triples",
            new=AsyncMock(return_value=[_rel("张三", "是", "主角")]),
        ):
            await build_graph_indexes(db, 1, ["c0"])
            await build_graph_indexes(db, 1, ["c0"])

        # 每次构建都先发两条 delete（实体表 + 关系表）。
        deletes = [s for s in db.statements if "delete" in str(s).lower()]
        assert len(deletes) == 4
        assert len(db.added) == 6  # 两轮各 2 实体 + 1 关系

    @pytest.mark.asyncio
    async def test_empty_extraction_keeps_empty(self):
        """抽取为空（LLM 未配置/失败）时图数据保持为空且不报错。"""
        db = _FakeSession()
        with patch(
            "app.services.graph.extract_graph_triples", new=AsyncMock(return_value=[])
        ):
            await build_graph_indexes(db, 1, ["c0"])

        assert db.added == []
        assert db.commits == 0


class TestClearAndCheck:
    @pytest.mark.asyncio
    async def test_clear_deletes_both_tables(self):
        db = _FakeSession()
        await clear_graph_indexes(db, 1)
        assert len(db.statements) == 2
        assert all("delete" in str(s).lower() for s in db.statements)

    @pytest.mark.asyncio
    async def test_document_has_graph_indexes(self):
        db = _FakeSession(rows=[1])
        assert await document_has_graph_indexes(db, 1) is True

        db = _FakeSession(rows=[])
        assert await document_has_graph_indexes(db, 1) is False


class TestGetNeighbors:
    def _session(self, rows):
        return _FakeSession(
            rows=rows,
            predicate=lambda r: r.document_id == 1
            and (r.subject == "张三" or r.object == "张三"),
        )

    @pytest.mark.asyncio
    async def test_out_and_in_neighbors(self):
        rows = [_rel("张三", "是", "主角"), _rel("李四", "击败", "张三")]
        neighbors = await get_neighbors(self._session(rows), 1, "张三")

        assert [n["name"] for n in neighbors] == ["主角", "李四"]
        assert neighbors[0]["direction"] == "out"
        assert neighbors[0]["relation"] == "是"
        assert neighbors[1]["direction"] == "in"
        assert neighbors[1]["relation"] == "击败"
        # 源文本块引用随邻居返回。
        assert all("chunk_index" in n and "content" in n for n in neighbors)

    @pytest.mark.asyncio
    async def test_self_loop_yields_both_directions(self):
        rows = [_rel("张三", "自述", "张三")]
        neighbors = await get_neighbors(self._session(rows), 1, "张三")
        assert len(neighbors) == 2

    @pytest.mark.asyncio
    async def test_unknown_entity_returns_empty(self):
        db = _FakeSession(rows=[_rel("张三", "是", "主角")], predicate=lambda r: False)
        assert await get_neighbors(db, 1, "王五") == []


class TestListAndCreate:
    @pytest.mark.asyncio
    async def test_list_triples_by_document(self):
        rows = [_rel("张三", "是", "主角"), _rel("李四", "击败", "张三")]
        db = _FakeSession(rows=rows, predicate=lambda r: r.document_id == 1)
        triples = await list_triples(db, 1)
        assert len(triples) == 2
        assert all(t.document_id == 1 for t in triples)

    @pytest.mark.asyncio
    async def test_create_triple_syncs_entities(self):
        db = _FakeSession()

        rel = await create_triple(
            db, 1, "张三", "是", "主角", chunk_index=2, content="c2"
        )

        assert rel.document_id == 1
        assert db.commits == 1
        entities = [o for o in db.added if isinstance(o, GraphEntity)]
        relations = [o for o in db.added if isinstance(o, GraphRelation)]
        assert len(relations) == 1
        assert {e.name for e in entities} == {"张三", "主角"}
        assert all(e.chunk_index == 2 and e.content == "c2" for e in entities)


class TestMockExtractTriplesResponse:
    """MockLLMProvider 的抽取输出与 E2E 断言基准保持一致。"""

    def test_fixed_response_contains_deterministic_triples(self):
        assert MOCK_EXTRACT_TRIPLES_RESPONSE == {
            "triples": [
                {"subject": "张三", "relation": "是", "object": "主角", "chunk": 0},
                {"subject": "李四", "relation": "击败", "object": "张三", "chunk": 0},
            ]
        }


def _make_doc(**kwargs):
    defaults = dict(
        id=5,
        filename="novel.txt",
        file_type="txt",
        size=100,
        file_path="/uploads/novel.txt",
        chunk_count=0,
        title="十日终焉",
        cover_image_path=None,
        status="pending",
        progress=0,
        error_message=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _patch_index_io(chunks=("c1", "c2")):
    """patch 后台索引的全部 I/O 边界（与 test_documents_service 同风格）。"""
    mock_provider = MagicMock()
    mock_provider.embed_texts = MagicMock(return_value=[[0.1], [0.2]])
    return [
        patch.object(document_service.DocumentParser, "parse", return_value="text body"),
        patch.object(document_service.TextChunker, "chunk", return_value=list(chunks)),
        patch.object(
            document_service, "get_embedding_provider", return_value=mock_provider
        ),
        patch.object(document_service.VectorStoreService, "insert"),
        patch.object(document_service.VectorStoreService, "save_document_text"),
        patch.object(document_service.VectorStoreService, "__init__", return_value=None),
    ]


class TestDocumentLifecycleIntegration:
    """#80：文档处理/删除/重索引生命周期中的图数据钩子。"""

    @pytest.mark.asyncio
    async def test_index_processing_builds_graph_indexes(self):
        doc = _make_doc()
        db = _FakeSession(rows=[doc])

        with patch(
            "app.services.graph.build_graph_indexes", new=AsyncMock()
        ) as mock_build, patch(
            "app.services.retrieval.indexing.build_metadata_indexes", new=AsyncMock()
        ), ExitStack() as stack:
            for p in _patch_index_io():
                stack.enter_context(p)
            await document_service._process_document_index(db, document_id=5)

        assert doc.status == "ready"
        # 图构建收到 document_id 与 chunks。
        assert mock_build.await_args.args[1] == 5
        assert mock_build.await_args.args[2] == ["c1", "c2"]

    @pytest.mark.asyncio
    async def test_graph_build_failure_does_not_block_ready(self):
        """抽取失败静默降级：文档仍 ready，不报错。"""
        doc = _make_doc()
        db = _FakeSession(rows=[doc])

        with patch(
            "app.services.graph.build_graph_indexes",
            new=AsyncMock(side_effect=RuntimeError("graph down")),
        ), patch(
            "app.services.retrieval.indexing.build_metadata_indexes", new=AsyncMock()
        ), ExitStack() as stack:
            for p in _patch_index_io():
                stack.enter_context(p)
            await document_service._process_document_index(db, document_id=5)

        assert doc.status == "ready"
        assert doc.error_message is None

    @pytest.mark.asyncio
    async def test_e2e_mock_injects_mock_llm(self):
        """#80：e2e_mock=True 时图构建注入 MockLLMProvider（E2E 确定性）。"""
        doc = _make_doc()
        db = _FakeSession(rows=[doc])

        with patch(
            "app.services.graph.build_graph_indexes", new=AsyncMock()
        ) as mock_build, patch(
            "app.services.retrieval.indexing.build_metadata_indexes", new=AsyncMock()
        ), ExitStack() as stack:
            for p in _patch_index_io():
                stack.enter_context(p)
            await document_service._process_document_index(
                db, document_id=5, e2e_mock=True
            )

        llm = mock_build.await_args.kwargs["llm"]
        assert isinstance(llm, MockLLMProvider)

    @pytest.mark.asyncio
    async def test_non_mock_passes_none_llm(self):
        doc = _make_doc()
        db = _FakeSession(rows=[doc])

        with patch(
            "app.services.graph.build_graph_indexes", new=AsyncMock()
        ) as mock_build, patch(
            "app.services.retrieval.indexing.build_metadata_indexes", new=AsyncMock()
        ), ExitStack() as stack:
            for p in _patch_index_io():
                stack.enter_context(p)
            await document_service._process_document_index(db, document_id=5)

        assert mock_build.await_args.kwargs["llm"] is None

    @pytest.mark.asyncio
    async def test_delete_document_clears_graph(self):
        doc = _make_doc()
        db = _FakeSession(rows=[doc])

        with patch.object(
            document_service.VectorStoreService, "delete_by_document_id"
        ), patch.object(
            document_service.VectorStoreService, "__init__", return_value=None
        ), patch(
            "app.services.retrieval.indexing.clear_metadata_indexes", new=AsyncMock()
        ), patch(
            "app.services.graph.clear_graph_indexes", new=AsyncMock()
        ) as mock_clear, patch("os.path.exists", return_value=False):
            await document_service.delete_document(db, document_id=5)

        mock_clear.assert_awaited_once_with(db, 5)
        assert db.commits == 1

    @pytest.mark.asyncio
    async def test_delete_graph_cleanup_failure_is_silent(self):
        """清理失败静默：不阻断文档删除。"""
        doc = _make_doc()
        db = _FakeSession(rows=[doc])

        with patch.object(
            document_service.VectorStoreService, "delete_by_document_id"
        ), patch.object(
            document_service.VectorStoreService, "__init__", return_value=None
        ), patch(
            "app.services.retrieval.indexing.clear_metadata_indexes", new=AsyncMock()
        ), patch(
            "app.services.graph.clear_graph_indexes",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ), patch("os.path.exists", return_value=False):
            await document_service.delete_document(db, document_id=5)

        assert db.commits == 1  # 删除仍提交

    @pytest.mark.asyncio
    async def test_requeue_clears_graph(self):
        doc = _make_doc(status="failed")
        db = _FakeSession(rows=[doc])

        with patch.object(
            document_service, "_delete_vectors_quietly"
        ), patch(
            "app.services.retrieval.indexing.clear_metadata_indexes", new=AsyncMock()
        ), patch(
            "app.services.graph.clear_graph_indexes", new=AsyncMock()
        ) as mock_clear:
            await document_service.requeue_document_index(db, document_id=5)

        mock_clear.assert_awaited_once_with(db, 5)
        assert doc.status == "pending"

    @pytest.mark.asyncio
    async def test_mark_failed_clears_graph(self):
        doc = _make_doc()
        db = _FakeSession(rows=[doc])

        with patch.object(
            document_service, "_delete_vectors_quietly"
        ), patch(
            "app.services.retrieval.indexing.clear_metadata_indexes", new=AsyncMock()
        ), patch(
            "app.services.graph.clear_graph_indexes", new=AsyncMock()
        ) as mock_clear:
            await document_service._mark_failed(db, 5, RuntimeError("boom"))

        mock_clear.assert_awaited_once_with(db, 5)
        assert doc.status == "failed"
