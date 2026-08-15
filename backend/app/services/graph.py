"""#80：GraphRAG 图谱构建——LLM 三元组抽取与图谱索引写入/查询。

抽取复用 LLM 服务 ``chat`` 接口；LLM 未配置或调用失败时静默降级
（返回空图，不阻断文档状态流转，与 #66 事件抽取契约一致）。

写入幂等：构建前先清后建；查询接口（一跳邻居 / 按文档查 / 写入三元组）
供测试与后续检索器使用。
"""
import json
import logging
import re
from typing import Dict, List, Optional, Tuple

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.graph import GraphEntity, GraphRelation

logger = logging.getLogger(__name__)

# prompt 任务标记：MockLLMProvider 靠它返回确定性的 E2E 三元组。
EXTRACT_TRIPLES_MARKER = "[EXTRACT_TRIPLES]"

# 抽取批大小：每批最多送这么多 chunk 给 LLM（与事件抽取 #66 一致）。
_GRAPH_BATCH_SIZE = 10


# ---------------------------------------------------------------------------
# 抽取（LLM）
# ---------------------------------------------------------------------------


async def extract_graph_triples(
    document_id: int,
    chunks: List[str],
    llm=None,
) -> List[GraphRelation]:
    """LLM 抽取实体关系三元组；LLM 未配置/调用失败返回空列表（静默降级）。

    ``llm`` 缺省时经 provider 工厂解析（LLM 未配置直接跳过）；注入时
    直接使用（单测 / E2E mock 走此路径）。
    """
    if llm is None:
        from app.services.llm import get_llm_provider, is_llm_configured

        if not is_llm_configured()[0]:
            logger.info("LLM 未配置：跳过图谱抽取（document %s）", document_id)
            return []
        llm = get_llm_provider(None)

    relations: List[GraphRelation] = []
    for batch_start in range(0, len(chunks), _GRAPH_BATCH_SIZE):
        batch = list(
            enumerate(
                chunks[batch_start : batch_start + _GRAPH_BATCH_SIZE],
                start=batch_start,
            )
        )
        if not batch:
            continue
        payload_text = "\n".join(f"[{i}] {text[:300]}" for i, text in batch)
        prompt = f"""{EXTRACT_TRIPLES_MARKER}
从小说片段中抽取实体关系三元组（如 人物-关系-人物），输出 JSON（不要输出其他内容）：
{{"triples": [{{"subject": "主语实体", "relation": "关系", "object": "宾语实体", "chunk": 片段编号}}]}}

片段：
{payload_text}"""
        try:
            raw = await llm.chat(
                messages=[{"role": "user", "content": prompt}], temperature=0.1
            )
            relations.extend(_parse_triple_result(document_id, raw, batch))
        except Exception as exc:  # noqa: BLE001 — 抽取失败跳过该批，不阻断
            logger.warning("图谱抽取失败（document %s）：%s", document_id, exc)
    return relations


def _parse_triple_result(
    document_id: int,
    raw: str,
    batch: List[Tuple[int, str]],
) -> List[GraphRelation]:
    """解析 LLM 抽取输出；非法输入返回空列表（静默降级）。"""
    match = re.search(r"\{.*\}", raw.strip(), re.DOTALL) if raw else None
    if not match:
        return []
    try:
        payload = json.loads(match.group(0))
    except (json.JSONDecodeError, TypeError):
        return []
    triples = payload.get("triples")
    if not isinstance(triples, list):
        return []
    relations: List[GraphRelation] = []
    for triple in triples:
        if not isinstance(triple, dict):
            continue
        subject = (triple.get("subject") or "").strip()
        relation = (triple.get("relation") or "").strip()
        obj = (triple.get("object") or "").strip()
        if not subject or not relation or not obj:
            continue
        chunk_index = triple.get("chunk")
        if not isinstance(chunk_index, int):
            continue
        content = next((text for idx, text in batch if idx == chunk_index), "")
        relations.append(
            GraphRelation(
                document_id=document_id,
                subject=subject,
                relation=relation,
                object=obj,
                chunk_index=chunk_index,
                content=content,
            )
        )
    return relations


# ---------------------------------------------------------------------------
# 实体行构建与去重
# ---------------------------------------------------------------------------


def build_entity_rows(
    document_id: int,
    relations: List[GraphRelation],
) -> List[GraphEntity]:
    """从三元组 subject/object 去重出实体行。

    实体在关系中首次出现时的 chunk 引用作为该实体的来源（证据回溯）；
    同一文档内同名实体只保留一行。
    """
    entities: Dict[str, GraphEntity] = {}
    for rel in relations:
        for name in (rel.subject, rel.object):
            if name in entities:
                continue
            entities[name] = GraphEntity(
                document_id=document_id,
                name=name,
                chunk_index=rel.chunk_index,
                content=rel.content,
            )
    return list(entities.values())


def _dedupe_relations(relations: List[GraphRelation]) -> List[GraphRelation]:
    """同一 (subject, relation, object) 只保留一条。

    多批抽取可能输出相同三元组（E2E mock 每批返回固定结果），去重避免
    关系表出现重复行。
    """
    seen: set = set()
    unique: List[GraphRelation] = []
    for rel in relations:
        key = (rel.subject, rel.relation, rel.object)
        if key in seen:
            continue
        seen.add(key)
        unique.append(rel)
    return unique


# ---------------------------------------------------------------------------
# 索引构建与清理（文档生命周期钩子）
# ---------------------------------------------------------------------------


async def build_graph_indexes(
    db: AsyncSession,
    document_id: int,
    chunks: List[str],
    llm=None,
) -> None:
    """构建图谱索引（幂等：先清后建）。

    抽取为空（LLM 未配置/失败/无三元组）时图数据保持为空且不报错——
    文档状态流转不受影响（抽取失败静默降级）。
    """
    await clear_graph_indexes(db, document_id)
    relations = _dedupe_relations(
        await extract_graph_triples(document_id, chunks, llm)
    )
    if not relations:
        return
    db.add_all(build_entity_rows(document_id, relations))
    db.add_all(relations)
    await db.commit()


async def clear_graph_indexes(db: AsyncSession, document_id: int) -> None:
    """清理某小说的全部图数据（删除/重建前调用）。"""
    for model in (GraphEntity, GraphRelation):
        await db.execute(delete(model).where(model.document_id == document_id))


async def document_has_graph_indexes(
    db: AsyncSession, document_id: int
) -> bool:
    """小说是否已有图数据（重建脚本按此跳过）。"""
    result = await db.execute(
        select(GraphRelation.id)
        .where(GraphRelation.document_id == document_id)
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# 查询与写入（供测试与后续检索器使用）
# ---------------------------------------------------------------------------


async def get_neighbors(
    db: AsyncSession,
    document_id: int,
    name: str,
) -> List[dict]:
    """按实体名查一跳邻居（含方向与源文本块引用）。

    subject == name 的关系为出边（邻居是 object）；object == name 的关系
    为入边（邻居是 subject）。自环关系同时产生出/入两条。
    """
    result = await db.execute(
        select(GraphRelation).where(
            GraphRelation.document_id == document_id,
            or_(GraphRelation.subject == name, GraphRelation.object == name),
        )
    )
    neighbors: List[dict] = []
    for rel in result.scalars().all():
        if rel.subject == name:
            neighbors.append(
                {
                    "name": rel.object,
                    "relation": rel.relation,
                    "direction": "out",
                    "chunk_index": rel.chunk_index,
                    "content": rel.content,
                }
            )
        if rel.object == name:
            neighbors.append(
                {
                    "name": rel.subject,
                    "relation": rel.relation,
                    "direction": "in",
                    "chunk_index": rel.chunk_index,
                    "content": rel.content,
                }
            )
    return neighbors


async def list_triples(
    db: AsyncSession,
    document_id: int,
) -> List[GraphRelation]:
    """按文档查全部三元组（含来源 chunk 引用）。"""
    result = await db.execute(
        select(GraphRelation).where(GraphRelation.document_id == document_id)
    )
    return list(result.scalars().all())


async def create_triple(
    db: AsyncSession,
    document_id: int,
    subject: str,
    relation: str,
    object_: str,
    chunk_index: int = 0,
    content: str = "",
) -> GraphRelation:
    """写入三元组并同步实体表（subject/object 缺失时补齐实体行）。

    供测试与后续检索器使用；与抽取写入共用同一数据契约。
    """
    relation_row = GraphRelation(
        document_id=document_id,
        subject=subject,
        relation=relation,
        object=object_,
        chunk_index=chunk_index,
        content=content,
    )
    db.add(relation_row)
    await db.flush()

    for name in (subject, object_):
        exists = await db.execute(
            select(GraphEntity.id)
            .where(
                GraphEntity.document_id == document_id,
                GraphEntity.name == name,
            )
            .limit(1)
        )
        if exists.scalar_one_or_none() is None:
            db.add(
                GraphEntity(
                    document_id=document_id,
                    name=name,
                    chunk_index=chunk_index,
                    content=content,
                )
            )

    await db.commit()
    await db.refresh(relation_row)
    return relation_row
