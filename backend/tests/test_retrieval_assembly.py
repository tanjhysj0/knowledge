"""#74：检索器装配层单测——settings 开关各组合下组装出的集合正确。

覆盖白名单未引入前的默认全量语义（五路全开）；settings 开关仅在此层
生效，公用模块不感知（后者在 test_retrieval_pipeline.py 验证）。
"""
from app.services.retrieval import assembly
from app.services.retrieval.assembly import build_retrievers
from app.services.retrieval.bm25 import BM25Retriever
from app.services.retrieval.dense import DenseRetriever
from app.services.retrieval.metadata import ChapterRetriever, EntityRetriever, EventRetriever

ALL_STRATEGIES = ["dense", "bm25", "entity", "event", "chapter"]


def _switch(name: str) -> str:
    return f"retrieval_{name}_enabled"


class TestBuildRetrievers:
    def test_all_switches_on_returns_full_set(self):
        """默认全量语义：五路全部组装。"""
        retrievers = build_retrievers()

        assert set(retrievers) == set(ALL_STRATEGIES)
        # key 与检索器自描述的 strategy 名一致
        for strategy, retriever in retrievers.items():
            assert retriever.strategy == strategy

    def test_assembles_expected_concrete_classes(self):
        retrievers = build_retrievers()

        assert isinstance(retrievers["dense"], DenseRetriever)
        assert isinstance(retrievers["bm25"], BM25Retriever)
        assert isinstance(retrievers["entity"], EntityRetriever)
        assert isinstance(retrievers["event"], EventRetriever)
        assert isinstance(retrievers["chapter"], ChapterRetriever)

    def test_single_switch_off_excludes_strategy(self, monkeypatch):
        monkeypatch.setattr(assembly.settings, _switch("dense"), False)

        retrievers = build_retrievers()

        assert set(retrievers) == set(ALL_STRATEGIES) - {"dense"}

    def test_partial_combination(self, monkeypatch):
        monkeypatch.setattr(assembly.settings, _switch("entity"), False)
        monkeypatch.setattr(assembly.settings, _switch("chapter"), False)

        retrievers = build_retrievers()

        assert set(retrievers) == {"dense", "bm25", "event"}

    def test_all_switches_off_returns_empty(self, monkeypatch):
        for name in ALL_STRATEGIES:
            monkeypatch.setattr(assembly.settings, _switch(name), False)

        assert build_retrievers() == {}

    def test_switch_restored_after_monkeypatch(self, monkeypatch):
        monkeypatch.setattr(assembly.settings, _switch("dense"), False)
        assert "dense" not in build_retrievers()

        monkeypatch.undo()

        assert "dense" in build_retrievers()

    def test_each_call_builds_fresh_instances(self):
        first, second = build_retrievers(), build_retrievers()
        assert first["dense"] is not second["dense"]
