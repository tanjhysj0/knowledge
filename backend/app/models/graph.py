"""#80：GraphRAG 图谱数据层——实体表 + 三元组关系表。

表随 ``Base.metadata.create_all`` 建表：

- ``graph_entities``：LLM 抽取出的实体（三元组 subject/object 去重），
  每条带来源 chunk 引用（证据回溯）。
- ``graph_relations``：subject/relation/object 三元组，每条带来源 chunk
  引用与文档归属（``document_id``），供一跳邻居查询与后续检索器使用。

全部按 ``document_id`` 建索引；删除小说时由调用方级联清理（与
:mod:`app.models.retrieval_index` 契约一致）。
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.core.database import Base


class GraphEntity(Base):
    """图谱实体：LLM 从文档块中抽取出的实体名（subject/object 去重）。"""

    __tablename__ = "graph_entities"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, nullable=False, index=True)
    name = Column(String(255), nullable=False, index=True)
    # 实体在关系中最先出现的 chunk 编号（证据回溯）。
    chunk_index = Column(Integer, nullable=False)
    # 源文本块引用：实体所在 chunk 的内容（证据回溯）。
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class GraphRelation(Base):
    """图谱三元组：subject → relation → object，带来源 chunk 引用。"""

    __tablename__ = "graph_relations"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, nullable=False, index=True)
    subject = Column(String(255), nullable=False, index=True)
    relation = Column(String(255), nullable=False)
    # #80：三元组宾语（与 subject 对称，均建索引支持双向一跳邻居查询）。
    object = Column(String(255), nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False)
    # 源文本块引用：三元组所在 chunk 的内容（证据回溯）。
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
