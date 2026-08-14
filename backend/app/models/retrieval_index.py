"""#66：混合检索的辅助索引表模型。

四张表随 ``Base.metadata.create_all`` 建表，与 Milvus dense 向量互补：

- ``bm25_chunks``：每 chunk 的 jieba 分词结果（JSON），检索时应用层算 BM25。
- ``chapter_anchors``：从原文"第X章"标题解析出的章节边界。
- ``entity_anchors``：jieba 词性标注抽取的人物/地点/专名（nr/ns/nz）。
- ``event_anchors``：LLM 抽取的剧情事件（LLM 不可用时该表为空，检索器降级）。

全部按 ``document_id`` 建索引；删除小说时由调用方级联清理。
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

from app.core.database import Base


class Bm25Chunk(Base):
    __tablename__ = "bm25_chunks"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    # jieba.lcut 后的词序列；JSONB 让 SQLite 单测环境也能建表（JSON 类型兜底）。
    tokens = Column(JSONB().with_variant(JSON, "sqlite"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class ChapterAnchor(Base):
    __tablename__ = "chapter_anchors"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, nullable=False, index=True)
    chapter_no = Column(Integer, nullable=False)
    title = Column(String(255), nullable=False)
    # 章节覆盖的 chunk 区间（半开区间 [chunk_start, chunk_end)）。
    chunk_start = Column(Integer, nullable=False)
    chunk_end = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class EntityAnchor(Base):
    __tablename__ = "entity_anchors"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, nullable=False, index=True)
    name = Column(String(255), nullable=False, index=True)
    # jieba.posseg 词性：nr（人名）/ns（地名）/nz（其他专名）。
    kind = Column(String(16), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class EventAnchor(Base):
    __tablename__ = "event_anchors"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, nullable=False, index=True)
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=False)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
