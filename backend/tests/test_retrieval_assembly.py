"""#74：检索器装配层单测——settings 开关各组合下组装出的集合正确。

覆盖白名单未引入前的默认全量语义（六路全开）；settings 开关仅在此层
生效，公用模块不感知（后者在 test_retrieval_pipeline.py 验证）。

#79：策略名→开关字段映射表删除——``strategy`` / ``switch`` 均由检索器类
自描述；:func:`enabled_strategy_names` 供 v1 接入层推导"当前启用全集"。
#81：新增 graph 路——默认全量六路，开关关闭即退出。
"""
from app.services.retrieval import assembly
from app.services.retrieval.assembly import build_retrievers, enabled_strategy_names
from app.services.retrieval.bm25 import BM25Retriever
from app.services.retrieval.dense import DenseRetriever
from app.services.retrieval.graph import GraphRetriever
from app.services.retrieval.metadata import ChapterRetriever, EntityRetriever, EventRetriever

ALL_STRATEGIES = ["dense", "bm25", "entity", "event", "chapter", "graph"]

# 检索器实现集（新增检索器在此登记；strategy/switch 均自描述）。
_RETRIEVER_CLASSES = [
    DenseRetriever,
    BM25Retriever,
    EntityRetriever,
    EventRetriever,
    ChapterRetriever,
    GraphRetriever,
]


def _switch(name: str) -> str:
    return f"retrieval_{name}_enabled"


class TestBuildRetrievers:
    def test_all_switches_on_returns_full_set(self):
        """默认全量语义：六路全部组装。"""
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
        assert isinstance(retrievers["graph"], GraphRetriever)

    def test_single_switch_off_excludes_strategy(self, monkeypatch):
        monkeypatch.setattr(assembly.settings, _switch("dense"), False)

        retrievers = build_retrievers()

        assert set(retrievers) == set(ALL_STRATEGIES) - {"dense"}

    def test_partial_combination(self, monkeypatch):
        monkeypatch.setattr(assembly.settings, _switch("entity"), False)
        monkeypatch.setattr(assembly.settings, _switch("chapter"), False)

        retrievers = build_retrievers()

        assert set(retrievers) == {"dense", "bm25", "event", "graph"}

    def test_graph_switch_off_excludes_strategy(self, monkeypatch):
        """#81：graph 开关关闭时该路完全退出装配（prompt 不可见/检索不调用）。"""
        monkeypatch.setattr(assembly.settings, _switch("graph"), False)

        retrievers = build_retrievers()

        assert set(retrievers) == set(ALL_STRATEGIES) - {"graph"}

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


class TestRetrieverSelfDescribedSwitch:
    """#79：策略名与 settings 开关字段名均由检索器类自描述（映射表已删除）。"""

    def test_each_class_self_describes_strategy_and_switch(self):
        assert not hasattr(assembly, "_STRATEGY_SWITCHES")
        for cls in _RETRIEVER_CLASSES:
            # switch 指向真实存在的 settings 字段，且与 strategy 命名一致
            assert getattr(assembly.settings, cls.switch) is True
            assert cls.switch == _switch(cls.strategy)

    def test_switch_off_excludes_strategy(self, monkeypatch):
        monkeypatch.setattr(assembly.settings, _switch("dense"), False)
        assert "dense" not in build_retrievers()


class TestEnabledStrategyNames:
    """#79：当前启用全集——v1 接入层动态白名单的来源。"""

    def test_default_returns_full_set_in_registry_order(self):
        assert enabled_strategy_names() == ALL_STRATEGIES

    def test_tracks_switch_off(self, monkeypatch):
        monkeypatch.setattr(assembly.settings, _switch("entity"), False)
        monkeypatch.setattr(assembly.settings, _switch("chapter"), False)
        assert enabled_strategy_names() == ["dense", "bm25", "event", "graph"]

    def test_all_switches_off_returns_empty(self, monkeypatch):
        for name in ALL_STRATEGIES:
            monkeypatch.setattr(assembly.settings, _switch(name), False)
        assert enabled_strategy_names() == []
