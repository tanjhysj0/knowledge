"""#74：检索器装配层——settings 开关 → 检索器集合（唯一感知 settings 的层）。

公用模块（RAGService / HybridRetrievalPipeline / EvidenceAgent）不 import、
不实例化任何具体检索器类，只依赖 :class:`app.services.retrieval.Retriever`
契约与 ``RetrievalHit``；本模块是"检索器实现集 + settings 开关"的唯一登记
与过滤点，组装出的 ``Dict[str, Retriever]``（key 为各检索器自描述的
``strategy`` 名）经构造注入公用模块。

#79：策略名的唯一登记点是检索器自身的自描述属性——``strategy`` 名与
settings 开关字段名（``switch``）均由检索器类自描述；本层登记表仅保留
一行类名登记（策略名 → 开关字段的映射表已删除）。

#73 起将在此层叠加接入层策略白名单过滤；settings 开关始终只在此层生效。
"""
from typing import Dict, List, Type

from app.core.config import get_settings
from app.services.retrieval import Retriever
from app.services.retrieval.bm25 import BM25Retriever
from app.services.retrieval.dense import DenseRetriever
from app.services.retrieval.graph import GraphRetriever
from app.services.retrieval.metadata import ChapterRetriever, EntityRetriever, EventRetriever

settings = get_settings()

# 检索器实现集（新增检索器 = 在此登记 + 自描述 strategy/switch 名，
# 公用模块 / planner / mock / 接入层零改动）。
_RETRIEVER_CLASSES: List[Type[Retriever]] = [
    DenseRetriever,
    BM25Retriever,
    EntityRetriever,
    EventRetriever,
    ChapterRetriever,
    GraphRetriever,
]


def build_retrievers() -> Dict[str, Retriever]:
    """按 settings 开关从实现集过滤组装检索器集合。

    key 取自检索器自描述的 ``strategy`` 名，开关字段名取各检索器自描述的
    ``switch`` 属性；entity/event 索引缺失不影响构建（检索器内部会降级为
    空结果）。
    """
    retrievers: Dict[str, Retriever] = {}
    for cls in _RETRIEVER_CLASSES:
        if not getattr(settings, cls.switch, True):
            continue
        retrievers[cls.strategy] = cls()
    return retrievers


def enabled_strategy_names() -> List[str]:
    """当前启用全集：settings 开关开启的已登记检索器 strategy 名列表。

    #79：v1 接入层白名单据此动态化——新增检索器 + 打开开关即自动进入 v1
    （忠实还原迁移前"默认生效策略集合"语义）；顺序与登记表一致。
    """
    return list(build_retrievers().keys())
