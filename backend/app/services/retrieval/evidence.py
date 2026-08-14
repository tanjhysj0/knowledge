"""#66：Evidence Pack——融合重排后的证据集合 + 证据循环元数据。"""
from dataclasses import dataclass, field
from typing import Any, Dict, List

from app.services.retrieval import RetrievalHit


@dataclass
class EvidencePack:
    """一次问答收集到的证据包。

    ``hits``：融合 + 重排后的 top-N 命中（score 为 RRF 分数）。
    ``sufficient``：证据代理（Evidence Agent）判定证据是否足够。
    ``iterations``：实际执行的补充检索轮次（首轮检索不计入）。
    ``note``：证据有限 / 达到上限等提示（拼入回答 prompt）。
    """

    hits: List[RetrievalHit] = field(default_factory=list)
    sufficient: bool = True
    iterations: int = 0
    note: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.hits

    def to_dict(self) -> Dict[str, Any]:
        """证据包摘要（SSE ``evidence`` 事件负载）。"""
        return {
            "hits": [hit.to_dict() for hit in self.hits],
            "sufficient": self.sufficient,
            "iterations": self.iterations,
            "note": self.note,
        }

    def sources(self) -> List[str]:
        """``["doc_<id>", ...]`` 形式（与旧 ``sources`` 契约兼容）。"""
        result: List[str] = []
        seen = set()
        for hit in self.hits:
            if hit.document_id in seen:
                continue
            seen.add(hit.document_id)
            result.append(f"doc_{hit.document_id}")
        return result
