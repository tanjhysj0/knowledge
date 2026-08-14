"""#66：Fusion（RRF）纯函数单测。"""
from app.services.retrieval import RetrievalHit
from app.services.retrieval.fusion import RRF_K, normalize_scores, rrf_fusion


def _hit(doc_id: int, chunk: int, strategy: str, score: float = 0.8, content: str = "x") -> RetrievalHit:
    return RetrievalHit(
        document_id=doc_id, chunk_index=chunk, content=content,
        score=score, strategy=strategy,
    )


class TestRRFFusion:
    def test_merges_and_ranks_across_strategies(self):
        groups = {
            "dense": [_hit(1, 0, "dense", 0.9), _hit(2, 0, "dense", 0.8)],
            "bm25": [_hit(3, 0, "bm25", 5.0), _hit(1, 0, "bm25", 4.0)],  # (1,0) 重复
        }
        fused = rrf_fusion(groups, top_n=10)
        # dense(1,0) rank0 = 1/61、bm25(3,0) rank0 = 1/61 → 同分；稳定排序按插入序
        keys = [(h.document_id, h.chunk_index) for h in fused]
        assert (1, 0) in keys
        assert (2, 0) in keys
        assert (3, 0) in keys
        # 分数即 RRF 值
        assert fused[0].score == 1 / (RRF_K + 1)

    def test_dedupes_same_chunk_keeping_highest_rrf(self):
        # (1,0) 在 dense 排第 1（rank 1）、在 bm25 排第 0（rank 0）→ 保留 bm25 高分
        groups = {
            "dense": [_hit(2, 0, "dense", 0.9), _hit(1, 0, "dense", 0.8)],
            "bm25": [_hit(1, 0, "bm25", 5.0)],
        }
        fused = rrf_fusion(groups, top_n=10)
        by_key = {(h.document_id, h.chunk_index): h for h in fused}
        assert len(by_key) == 2
        assert by_key[(1, 0)].strategy == "bm25"
        assert by_key[(1, 0)].score == 1 / (RRF_K + 1)

    def test_truncates_to_top_n(self):
        groups = {"dense": [_hit(i, 0, "dense", 0.9) for i in range(10)]}
        fused = rrf_fusion(groups, top_n=3)
        assert len(fused) == 3
        assert [h.document_id for h in fused] == [0, 1, 2]

    def test_applies_strategy_weights(self):
        groups = {"dense": [_hit(1, 0, "dense", 0.9)], "bm25": [_hit(2, 0, "bm25", 5.0)]}
        fused = rrf_fusion(groups, top_n=10, weights={"dense": 10.0, "bm25": 1.0})
        assert fused[0].document_id == 1  # dense 高权重排前

    def test_unknown_strategy_defaults_to_weight_one(self):
        groups = {"dense": [_hit(1, 0, "dense", 0.9)], "weird": [_hit(2, 0, "weird", 5.0)]}
        fused = rrf_fusion(groups, top_n=10)
        assert len(fused) == 2

    def test_empty_groups_returns_empty(self):
        assert rrf_fusion({}, top_n=5) == []

    def test_keeps_chapter_hint_from_lower_scored_duplicate(self):
        chapter_hit = _hit(1, 0, "chapter", 1.0)
        chapter_hit.chapter = "第3章"
        groups = {
            "dense": [_hit(2, 0, "dense", 0.9), _hit(1, 0, "dense", 0.8)],
            "chapter": [chapter_hit],
        }
        fused = rrf_fusion(groups, top_n=10)
        by_key = {(h.document_id, h.chunk_index): h for h in fused}
        assert by_key[(1, 0)].chapter == "第3章"


class TestNormalizeScores:
    def test_normalizes_to_unit_range(self):
        hits = [_hit(1, 0, "dense", 0.01), _hit(2, 0, "dense", 0.02), _hit(3, 0, "dense", 0.04)]
        normalized = normalize_scores(hits)
        assert max(h.score for h in normalized) == 1.0
        assert normalized[0].score < normalized[-1].score

    def test_empty_returns_empty(self):
        assert normalize_scores([]) == []
