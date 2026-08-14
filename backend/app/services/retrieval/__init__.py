"""#66：混合检索（Hybrid Retrieval）模块。

统一 ``Retriever`` 协议：五路检索器（dense / bm25 / entity / event /
chapter）各自实现 ``retrieve(query, document_ids, top_k) -> List[RetrievalHit]``，
由 :class:`app.services.retrieval.pipeline.HybridRetrievalPipeline` 编排，
RRF 融合后进入 Evidence Pack。
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


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
    """五路检索器的统一协议。

    检索异常由实现内部吞掉并返回空列表——某一路不可用不影响整体问答
    （PRD：服务稳定降级）。
    """

    name: str

    async def retrieve(
        self,
        query: str,
        document_ids: Optional[List[int]] = None,
        top_k: int = 5,
    ) -> List[RetrievalHit]: ...
