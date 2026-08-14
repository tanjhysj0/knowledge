"""#66：辅助索引构建——章节 / 实体 / 事件 / BM25 tokens 的抽取与写入。

构建失败不阻断上传（PRD 兼容性要求）：调用方用 try/except 包裹并记录
日志，事后可用 :mod:`scripts.rebuild_metadata_indexes` 重建。

抽取器选型（见 ADR-0005）：

- 章节：正则解析"第X章/节/回/卷"标题行（确定性，无需 LLM）。
- 实体：jieba.posseg 词性标注提取 nr/ns/nz 专名（确定性，无需 LLM）。
- 事件：LLM 抽取（复用 Provider 工厂）；LLM 未配置时直接跳过。
- BM25：jieba 分词 tokens 落库（检索时应用层算分）。
"""
import logging
import re
from collections import Counter
from typing import Dict, List, Optional, Tuple

import jieba.posseg as pseg
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.retrieval_index import (
    Bm25Chunk,
    ChapterAnchor,
    EntityAnchor,
    EventAnchor,
)
from app.services.retrieval.bm25 import tokenize

logger = logging.getLogger(__name__)

# 章节标题行模式：允许缩进/标点前缀，捕获编号与标题。
_CHAPTER_LINE_RE = re.compile(
    r"^\s*第\s*([0-9零一二三四五六七八九十百千]+)\s*[章节回卷部]\s*[：:、.\s]*(.*)$"
)
_CHAPTER_LINE_EN_RE = re.compile(r"^\s*[Cc]hapter\s+(\d+)\s*[：:.\s]*(.*)$")

# 实体抽取的词性：人名 / 地名 / 其他专名。
_ENTITY_FLAGS = ("nr", "ns", "nz")

# 事件抽取：每批最多送这么多 chunk 给 LLM。
_EVENT_BATCH_SIZE = 10

_EVENT_MARKER = "[EXTRACT_EVENTS]"

# 中文数字映射（章节号解析）。
_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "百": 100, "千": 1000}


def parse_chapter_number(raw: str) -> int:
    """中文/阿拉伯章节号 → int（``三`` → 3、``十二`` → 12）。"""
    if raw.isdigit():
        return int(raw)
    total, section, current = 0, 0, 0
    for char in raw:
        digit = _CN_DIGITS.get(char)
        if digit is None:
            continue
        if digit >= 10:
            section = (current or 1) * digit
            total += section
            current = 0
        else:
            current = digit
    return total + current


def extract_chapter_titles(text: str) -> List[Tuple[int, str]]:
    """从原文解析章节标题，返回 ``[(chapter_no, title), ...]``（按出现顺序）。"""
    titles: List[Tuple[int, str]] = []
    seen: set = set()
    for line in text.splitlines():
        for pattern, english in ((_CHAPTER_LINE_RE, False), (_CHAPTER_LINE_EN_RE, True)):
            match = pattern.match(line.strip())
            if not match:
                continue
            number = parse_chapter_number(match.group(1)) if not english else int(match.group(1))
            title = (match.group(2) or "").strip() or f"第{number}章"
            if number in seen:
                continue
            seen.add(number)
            titles.append((number, title))
            break
    return titles


def build_chapter_anchors(
    document_id: int, text: str, chunks: List[str]
) -> List[ChapterAnchor]:
    """把章节标题映射到 chunk 区间，构造 ChapterAnchor 列表。

    标题在哪个 chunk 中出现，该 chunk 即本章起点；下一章起点为本章
    终点；解析不到起点时跳过该章（保守，不产生错误区间）。
    """
    titles = extract_chapter_titles(text)
    if not titles:
        return []

    starts: List[int] = []
    for _, title in titles:
        found = -1
        for index, chunk in enumerate(chunks):
            if title[:8] in chunk:  # 标题前 8 字足够定位，避免全文匹配
                found = index
                break
        if found >= 0:
            starts.append(found)

    anchors: List[ChapterAnchor] = []
    for i, (chapter_no, title) in enumerate(titles):
        if i >= len(starts):
            break
        start = starts[i]
        end = starts[i + 1] if i + 1 < len(starts) else len(chunks)
        if end <= start:
            end = min(start + 1, len(chunks))
        anchors.append(
            ChapterAnchor(
                document_id=document_id,
                chapter_no=chapter_no,
                title=title,
                chunk_start=start,
                chunk_end=end,
            )
        )
    return anchors


def extract_entity_anchors(
    document_id: int, chunks: List[str]
) -> List[EntityAnchor]:
    """jieba 词性标注抽取实体锚点（每个实体在每个 chunk 至多一条）。

    只保留全书出现 ≥ 2 次的实体：只出现一次的专名多为词性误标，
    同时避免索引表行数爆炸（小说语料每 chunk 专名可达数十个）。
    """
    counters: Dict[str, Counter] = {flag: Counter() for flag in _ENTITY_FLAGS}
    per_chunk: List[set] = [set() for _ in chunks]
    for chunk_index, chunk in enumerate(chunks):
        for word, flag in pseg.cut(chunk):
            if flag not in _ENTITY_FLAGS or len(word) < 2:
                continue
            counters[flag][word] += 1
            per_chunk[chunk_index].add((word, flag))

    frequent = {
        word
        for flag, counter in counters.items()
        for word, count in counter.items()
        if count >= 2
    }

    anchors: List[EntityAnchor] = []
    for chunk_index, pairs in enumerate(per_chunk):
        for word, flag in pairs:
            if word not in frequent:
                continue
            anchors.append(
                EntityAnchor(
                    document_id=document_id,
                    name=word,
                    kind=flag,
                    chunk_index=chunk_index,
                    content=chunks[chunk_index],
                )
            )
    return anchors


async def extract_event_anchors(
    document_id: int, chunks: List[str], llm=None
) -> List[EventAnchor]:
    """LLM 抽取剧情事件；LLM 未配置/调用失败返回空列表（不阻断上传）。"""
    if llm is None:
        from app.services.llm import get_llm_provider, is_llm_configured

        if not is_llm_configured()[0]:
            logger.info("LLM 未配置：跳过事件抽取（document %s）", document_id)
            return []
        llm = get_llm_provider(None)

    anchors: List[EventAnchor] = []
    for batch_start in range(0, len(chunks), _EVENT_BATCH_SIZE):
        batch = list(enumerate(chunks[batch_start : batch_start + _EVENT_BATCH_SIZE], start=batch_start))
        if not batch:
            continue
        payload_text = "\n".join(f"[{i}] {text[:300]}" for i, text in batch)
        prompt = f"""{_EVENT_MARKER}
从小说片段中抽取剧情事件（如 大战/死亡/重逢/叛变），输出 JSON（不要输出其他内容）：
{{"events": [{{"name": "事件名", "description": "一句话描述", "chunk": 事件所在的片段编号}}]}}

片段：
{payload_text}"""
        try:
            raw = await llm.chat(
                messages=[{"role": "user", "content": prompt}], temperature=0.1
            )
            anchors.extend(
                _parse_event_result(document_id, raw, batch)
            )
        except Exception as exc:  # noqa: BLE001 — 事件抽取失败跳过，不阻断
            logger.warning("事件抽取失败（document %s）：%s", document_id, exc)
    return anchors


def _parse_event_result(
    document_id: int, raw: str, batch: List[Tuple[int, str]]
) -> List[EventAnchor]:
    import json as _json

    match = re.search(r"\{.*\}", raw.strip(), re.DOTALL) if raw else None
    if not match:
        return []
    try:
        payload = _json.loads(match.group(0))
    except (_json.JSONDecodeError, TypeError):
        return []
    events = payload.get("events")
    if not isinstance(events, list):
        return []
    anchors: List[EventAnchor] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        name = (event.get("name") or "").strip()
        if not name:
            continue
        chunk_index = event.get("chunk")
        if not isinstance(chunk_index, int):
            continue
        content = next((text for idx, text in batch if idx == chunk_index), "")
        anchors.append(
            EventAnchor(
                document_id=document_id,
                name=name,
                description=(event.get("description") or "").strip(),
                chunk_index=chunk_index,
                content=content,
            )
        )
    return anchors


def build_bm25_chunks(
    document_id: int, chunks: List[str]
) -> List[Bm25Chunk]:
    """jieba 分词 tokens 落库（检索时应用层算 BM25）。"""
    rows: List[Bm25Chunk] = []
    for index, chunk in enumerate(chunks):
        rows.append(
            Bm25Chunk(
                document_id=document_id,
                chunk_index=index,
                content=chunk,
                tokens=tokenize(chunk),
            )
        )
    return rows


async def build_metadata_indexes(
    db: AsyncSession, document_id: int, text: str, chunks: List[str]
) -> None:
    """构建四类辅助索引（幂等：先清后建）；任一步失败向上抛由调用方兜底。"""
    await clear_metadata_indexes(db, document_id)

    db.add_all(build_chapter_anchors(document_id, text, chunks))
    db.add_all(build_bm25_chunks(document_id, chunks))
    await db.flush()

    # 实体抽取是 CPU 密集 + jieba 首次加载词典较慢，丢线程池。
    import asyncio

    loop = asyncio.get_running_loop()
    entity_anchors = await loop.run_in_executor(
        None, extract_entity_anchors, document_id, chunks
    )
    db.add_all(entity_anchors)

    event_anchors = await extract_event_anchors(document_id, chunks)
    db.add_all(event_anchors)
    await db.commit()


async def clear_metadata_indexes(db: AsyncSession, document_id: int) -> None:
    """清理某小说的全部辅助索引（删除/重建前调用）。"""
    for model in (Bm25Chunk, ChapterAnchor, EntityAnchor, EventAnchor):
        await db.execute(
            delete(model).where(model.document_id == document_id)
        )


async def document_has_metadata_indexes(
    db: AsyncSession, document_id: int
) -> bool:
    """小说是否已有 BM25 索引（重建脚本按此跳过）。"""
    result = await db.execute(
        select(Bm25Chunk.id).where(Bm25Chunk.document_id == document_id).limit(1)
    )
    return result.scalar_one_or_none() is not None
