"""#66：Query Planner——LLM 把当前问题 + 会话历史解析为 QueryPlan。

LLM 调用走 :mod:`app.services.llm.get_llm_provider` 工厂（不新增全局单
例）；解析失败时降级为"原问题单查询 + 全策略"的保守计划，保证管线在
LLM 不可用时仍然可用。

#79：可注入"生效检索器对象集合"（``Dict[str, Retriever]``，仅依赖
:class:`Retriever` 契约与各对象自描述的 ``strategy`` 名，不感知具体策略
名 / settings 开关）——注入后 prompt 动态生成可用策略列表（不写死策略
名），解析与降级均按可用集合过滤；未注入时行为与 #66 完全一致（全量五路）。
"""
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.services.llm import LLMProvider
from app.services.retrieval import Retriever

# prompt 中的任务标记：MockLLMProvider 靠它返回确定性的 E2E 计划。
PLANNER_MARKER = "[QUERY_PLAN]"

# 全部五路策略（planner 降级 / 未显式指定时启用集合）。
ALL_STRATEGIES = ["dense", "bm25", "entity", "event", "chapter"]

# prompt 中动态生成可用策略列表所在行的前缀（MockLLMProvider 靠它按可用
# 策略返回确定性的 E2E 计划；prompt 中不写死任何策略名）。
AVAILABLE_STRATEGIES_LINE = "可用策略列表："


@dataclass
class QueryPlan:
    """查询计划：子查询 + 实体/事件/章节线索 + 启用策略。"""

    sub_queries: List[str] = field(default_factory=list)
    entities: List[str] = field(default_factory=list)
    events: List[str] = field(default_factory=list)
    chapter_hints: List[str] = field(default_factory=list)
    strategies: List[str] = field(default_factory=lambda: list(ALL_STRATEGIES))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sub_queries": self.sub_queries,
            "entities": self.entities,
            "events": self.events,
            "chapter_hints": self.chapter_hints,
            "strategies": self.strategies,
        }


def _fallback_plan(
    question: str, available_strategies: Optional[List[str]] = None
) -> QueryPlan:
    """LLM 不可用/输出非法时的保守计划：原问题单查询 + 可用策略。

    #79：``available_strategies`` 未指定（``None``）时回落全量五路（与 #66 一致）。
    """
    strategies = (
        list(available_strategies)
        if available_strategies is not None
        else list(ALL_STRATEGIES)
    )
    return QueryPlan(sub_queries=[question], strategies=strategies)


def _as_str_list(items) -> List[str]:
    """LLM 输出列表 → 非空字符串列表（非法元素类型直接丢弃，保证
    下游 ``' '.join`` 不会因混合类型报错）。"""
    if not isinstance(items, list):
        return []
    return [x.strip() for x in items if isinstance(x, str) and x.strip()]


def parse_query_plan(
    raw: str,
    fallback_question: str,
    available_strategies: Optional[List[str]] = None,
) -> QueryPlan:
    """解析 LLM 输出 JSON 为 QueryPlan；非法输入降级为保守计划。

    纯函数，便于单测。容忍 LLM 在 JSON 外包裹 markdown 代码块。

    #79：``available_strategies`` 为当前生效的可用策略集合（``None`` 等价
    于全量五路）——LLM 建议的越界策略被过滤，strategies 为空或未指定时
    回落到可用集合（而非全量）。
    """
    if not raw:
        return _fallback_plan(fallback_question, available_strategies)
    cleaned = raw.strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(0)
    try:
        payload = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        return _fallback_plan(fallback_question, available_strategies)

    allowed = (
        set(available_strategies)
        if available_strategies is not None
        else set(ALL_STRATEGIES)
    )
    strategies = [s for s in (payload.get("strategies") or []) if s in allowed]
    fallback = (
        list(available_strategies)
        if available_strategies is not None
        else list(ALL_STRATEGIES)
    )
    plan = QueryPlan(
        sub_queries=_as_str_list(payload.get("sub_queries")) or [fallback_question],
        entities=_as_str_list(payload.get("entities")),
        events=_as_str_list(payload.get("events")),
        chapter_hints=_as_str_list(payload.get("chapter_hints")),
        strategies=strategies or fallback,
    )
    return plan


class QueryPlanner:
    """LLM 驱动的查询规划器。

    ``llm`` 可注入（单测 mock）；默认每次调用经 :func:`get_llm_provider`
    工厂解析，设置页配置对规划调用即时生效。

    #79：``retrievers`` 为生效检索器对象集合（``Dict[str, Retriever]``，
    仅依赖 :class:`Retriever` 契约与自描述 ``strategy`` 名）——注入后
    prompt 动态生成可用策略列表并指示只建议可用策略；未注入时行为与 #66
    完全一致（全量五路，现有单测 / E2E 不回归）。
    """

    def __init__(
        self,
        llm: Optional[LLMProvider] = None,
        retrievers: Optional[Dict[str, Retriever]] = None,
    ):
        self._llm = llm
        self._retrievers = retrievers

    def _resolve_llm(self) -> LLMProvider:
        if self._llm is not None:
            return self._llm
        from app.services.llm import get_llm_provider

        return get_llm_provider(None)

    def _available_strategies(self) -> List[str]:
        """当前生效的可用策略列表：注入集合的自描述 strategy 名。

        未注入时返回全量五路（#66 行为）；注入集合为空则返回空列表。
        """
        if self._retrievers is None:
            return list(ALL_STRATEGIES)
        return [r.strategy for r in self._retrievers.values()]

    def _build_prompt(self, question: str, history: List[Dict[str, str]]) -> str:
        history_text = "\n".join(
            f"{msg['role']}: {msg['content']}" for msg in history[-6:]
        )
        available = ", ".join(self._available_strategies())
        return f"""{PLANNER_MARKER}
你是文档问答的查询规划器。把用户问题拆解为检索计划，输出 JSON（不要输出其他内容）：

{AVAILABLE_STRATEGIES_LINE}{available}

{{
  "sub_queries": ["检索用的子查询，1-3 个；多轮追问中的指代（他/她/那里）要结合历史替换成具体名称"],
  "entities": ["问题指向的人物/地点/物品专名（无则为空数组）"],
  "events": ["问题指向的剧情事件关键词（如 某某大战/某某之死，无则为空数组）"],
  "chapter_hints": ["问题提到的章节线索（无则为空数组）"],
  "strategies": ["只从上方可用策略列表中选取（全部可用就全列）"]
}}

约束：只建议上方可用策略列表内的策略；entities/events/chapter_hints 仅在
对应检索策略可用时提供线索，不可用策略对应的线索一律留空数组。

对话历史：
{history_text or "（无）"}

用户问题：{question}"""

    async def plan(
        self, question: str, history: Optional[List[Dict[str, str]]] = None
    ) -> QueryPlan:
        """解析问题为 QueryPlan；LLM 异常/解析失败降级为保守计划。"""
        history = history or []
        try:
            raw = await self._resolve_llm().chat(
                messages=[
                    {
                        "role": "user",
                        "content": self._build_prompt(question, history),
                    }
                ],
                temperature=0.2,
            )
        except Exception:  # noqa: BLE001 — 规划失败降级，不阻断问答
            return _fallback_plan(question, self._available_strategies())
        return parse_query_plan(raw, question, self._available_strategies())
