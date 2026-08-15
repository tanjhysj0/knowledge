"""#74：检索器装配层——settings 开关 → 检索器集合（唯一感知 settings 的层）。

公用模块（RAGService / HybridRetrievalPipeline / EvidenceAgent）不 import、
不实例化任何具体检索器类，只依赖 :class:`app.services.retrieval.Retriever`
契约与 ``RetrievalHit``；本模块是"检索器实现集 + settings 开关"的唯一登记
与过滤点，组装出的 ``Dict[str, Retriever]``（key 为各检索器自描述的
``strategy`` 名）经构造注入公用模块。

#73 起将在此层叠加接入层策略白名单过滤；settings 开关始终只在此层生效。
"""
from typing import Dict, List, Type

from app.core.config import get_settings
from app.services.retrieval import Retriever
from app.services.retrieval.bm25 import BM25Retriever
from app.services.retrieval.dense import DenseRetriever
from app.services.retrieval.metadata import ChapterRetriever, EntityRetriever, EventRetriever

settings = get_settings()

# 检索器实现集（新增检索器 = 在此登记 + 自描述 strategy 名，公用模块零改动）。
_RETRIEVER_CLASSES: List[Type[Retriever]] = [
    DenseRetriever,
    BM25Retriever,
    EntityRetriever,
    EventRetriever,
    ChapterRetriever,
]

# 策略名 → settings 开关字段名（仅装配层感知，公用模块不感知）。
_STRATEGY_SWITCHES = {
    "dense": "retrieval_dense_enabled",
    "bm25": "retrieval_bm25_enabled",
    "entity": "retrieval_entity_enabled",
    "event": "retrieval_event_enabled",
    "chapter": "retrieval_chapter_enabled",
}


def build_retrievers() -> Dict[str, Retriever]:
    """按 settings 开关从实现集过滤组装检索器集合。

    key 取自检索器自描述的 ``strategy`` 名；entity/event 索引缺失不影响
    构建（检索器内部会降级为空结果）。
    """
    retrievers: Dict[str, Retriever] = {}
    for cls in _RETRIEVER_CLASSES:
        if not getattr(settings, _STRATEGY_SWITCHES[cls.strategy], True):
            continue
        retrievers[cls.strategy] = cls()
    return retrievers
