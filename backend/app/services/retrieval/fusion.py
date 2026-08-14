"""#66：RRF（Reciprocal Rank Fusion）多路结果融合（纯函数，无外部依赖）。"""
from typing import Dict, Iterable, List

from app.services.retrieval import RetrievalHit

# RRF 平滑常数（经典取值 60）。
RRF_K = 60

# 每路命中内部按 score 降序排列后的归一化打分：rank 越小分越高。
_DEFAULT_WEIGHTS = {"dense": 1.0, "bm25": 1.0, "entity": 1.0, "event": 1.0, "chapter": 1.0}


def _dedupe_key(hit: RetrievalHit) -> tuple:
    return (hit.document_id, hit.chunk_index)


def rrf_fusion(
    hit_groups: Dict[str, List[RetrievalHit]],
    top_n: int,
    weights: Dict[str, float] | None = None,
    k: int = RRF_K,
) -> List[RetrievalHit]:
    """按 RRF 合并多路命中并去重。

    :param hit_groups: ``{strategy: hits}``（各路 hits 应按自身分数降序）。
    :param top_n: 融合后保留条数。
    :param weights: 各路权重（缺省 1.0）；未列出的策略按 1.0 计。
    :param k: RRF 平滑常数。

    同一 (document_id, chunk_index) 只保留最高 RRF 分的那条，保留其
    ``strategy`` / ``chapter`` 元数据；融合分数写入 ``score``。
    """
    merged: Dict[tuple, RetrievalHit] = {}
    for strategy, hits in hit_groups.items():
        weight = (weights or _DEFAULT_WEIGHTS).get(strategy, 1.0)
        for rank, hit in enumerate(hits):
            key = _dedupe_key(hit)
            rrf = weight / (k + rank + 1)
            existing = merged.get(key)
            if existing is None or rrf > existing.score:
                hit.score = rrf
                merged[key] = hit
            elif existing is not hit and hit.chapter and not existing.chapter:
                # 相同 key 低分命中携带章节线索时补充到高分命中上。
                existing.chapter = hit.chapter

    ranked = sorted(merged.values(), key=lambda h: h.score, reverse=True)
    return ranked[:top_n]


def normalize_scores(hits: Iterable[RetrievalHit]) -> List[RetrievalHit]:
    """RRF 分数归一化到 [0, 1]（供 evidence 事件展示，分数只用于排序对比）。"""
    result = list(hits)
    if not result:
        return result
    max_score = max(h.score for h in result)
    if max_score <= 0:
        return result
    for hit in result:
        hit.score = hit.score / max_score
    return result
