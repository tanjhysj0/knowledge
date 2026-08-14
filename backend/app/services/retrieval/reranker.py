"""#66：Reranker——对融合结果 LLM 重排（不可用时直通不过滤）。

复用现有 LLM Provider 工厂；LLM 异常或输出非法时原样返回输入顺序
（直通），保证重排环节不是问答可用性的单点。
"""
import json
import re
from typing import List, Optional

from app.services.llm import LLMProvider
from app.services.retrieval import RetrievalHit

# prompt 任务标记：MockLLMProvider 靠它返回确定性的 E2E 重排结果。
RERANK_MARKER = "[RERANK]"


def parse_rerank_result(
    raw: str, hits: List[RetrievalHit]
) -> List[RetrievalHit]:
    """解析重排 LLM 输出；非法输入直通原序。纯函数，便于单测。

    期望 JSON：``{"order": [2, 0, 1], "reject": [1]}``——``order`` 为
    重排后的索引序列，``reject`` 为剔除的索引（噪声）。
    """
    if not raw:
        return hits
    cleaned = raw.strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(0)
    try:
        payload = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        return hits
    order = payload.get("order")
    if not isinstance(order, list):
        return hits
    reject = set(payload.get("reject") or [])
    reranked: List[RetrievalHit] = []
    seen = set()
    for index in order:
        if index in seen or index in reject:
            continue
        if isinstance(index, int) and 0 <= index < len(hits):
            seen.add(index)
            reranked.append(hits[index])
    # 未出现在 order 中的命中（未被剔除）按原序补到末尾。
    for index, hit in enumerate(hits):
        if index not in seen and index not in reject:
            reranked.append(hit)
    return reranked


class LLMReranker:
    """LLM 重排器：``rerank(query, hits)`` 返回重排后的命中。"""

    def __init__(self, llm: Optional[LLMProvider] = None):
        self._llm = llm

    def _resolve_llm(self) -> LLMProvider:
        if self._llm is not None:
            return self._llm
        from app.services.llm import get_llm_provider

        return get_llm_provider(None)

    def _build_prompt(self, query: str, hits: List[RetrievalHit]) -> str:
        candidates = "\n\n".join(
            f"[{i}] {hit.content[:300]}" for i, hit in enumerate(hits)
        )
        return f"""{RERANK_MARKER}
你是检索结果重排器。按与问题的相关性重排候选段落，剔除无关噪声，输出 JSON（不要输出其他内容）：

{{"order": [相关性从高到低的候选索引], "reject": [应剔除的候选索引]}}

问题：{query}

候选段落：
{candidates}"""

    async def rerank(
        self, query: str, hits: List[RetrievalHit]
    ) -> List[RetrievalHit]:
        """重排命中；单条或空列表无需调用 LLM 直通返回。"""
        if len(hits) <= 1:
            return hits
        try:
            raw = await self._resolve_llm().chat(
                messages=[
                    {
                        "role": "user",
                        "content": self._build_prompt(query, hits),
                    }
                ],
                temperature=0.0,
            )
        except Exception:  # noqa: BLE001 — 重排失败直通
            return hits
        return parse_rerank_result(raw, hits)
