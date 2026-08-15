"""#66：混合检索（Hybrid Retrieval）模块。

统一 ``Retriever`` 协议：五路检索器（dense / bm25 / entity / event /
chapter）各自实现 ``retrieve(query, document_ids, top_k) -> List[RetrievalHit]``，
由 :class:`app.services.retrieval.pipeline.HybridRetrievalPipeline` 编排，
RRF 融合后进入 Evidence Pack。

#74：检索器自描述 ``strategy`` 名（装配层以它为 key 组装集合）；
QueryPlan 线索由各检索器经可选 ``decorate_query(query, plan)`` 钩子自行
消费，管线不感知任何具体策略名。
"""
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from app.services.retrieval.planner import QueryPlan


@dataclass
class RetrievalHit:
    """单条检索命中：内容 + 来源元数据。

    ``score`` 为检索器内部的相关性分数（越大越相关）；融合阶段按 rank
    重算，最终证据包里的 ``score`` 是 RRF 分数。``chapter`` 为章节线索
    （chapter/event 策略填充，其余策略可为空）。
    """

    document_id: int
    chunk_index: int
    content: str
    score: float
    strategy: str
    chapter: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document_id": self.document_id,
            "chunk_index": self.chunk_index,
            "content": self.content,
            "score": self.score,
            "strategy": self.strategy,
            "chapter": self.chapter,
        }


@runtime_checkable
class Retriever(Protocol):
    """五路检索器的统一协议（#74：公用模块只依赖本契约与 RetrievalHit）。

    检索异常由实现内部吞掉并返回空列表——某一路不可用不影响整体问答
    （PRD：服务稳定降级）。

    ``strategy`` 为检索器自描述的策略名：装配层以它为 key 组装检索器
    集合（``Dict[str, Retriever]``）并构造注入公用模块；``decorate_query``
    为可选钩子——检索器自行决定是否消费 QueryPlan 的实体/事件/章节线索，
    未实现钩子的检索器由管线透传原始 query。
    """

    strategy: str

    async def retrieve(
        self,
        query: str,
        document_ids: Optional[List[int]] = None,
        top_k: int = 5,
    ) -> List[RetrievalHit]: ...

    def decorate_query(self, query: str, plan: "QueryPlan") -> str:
        """把 QueryPlan 线索拼进检索词；默认透传原始 query（可选钩子）。"""
        return query
