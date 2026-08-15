"""#80：图谱查询/写入路由层——仅做 HTTP 适配、依赖注入和服务调用。

端点供测试与后续检索器使用：按实体名查一跳邻居（含源文本块引用）、
按文档查三元组、写入三元组（同步实体表）。
"""
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.schemas import (
    GraphNeighborsResponse,
    GraphRelationCreate,
    GraphRelationResponse,
)
from app.services import graph as graph_service

router = APIRouter()


@router.get("/neighbors", response_model=GraphNeighborsResponse)
async def graph_neighbors(
    document_id: int,
    name: str,
    db: AsyncSession = Depends(get_db),
):
    """按实体名查一跳邻居（含方向与源文本块引用）。"""
    neighbors = await graph_service.get_neighbors(db, document_id, name)
    return GraphNeighborsResponse(name=name, neighbors=neighbors)


@router.get("/triples", response_model=List[GraphRelationResponse])
async def graph_triples(
    document_id: int,
    db: AsyncSession = Depends(get_db),
):
    """按文档查全部三元组（含来源 chunk 引用）。"""
    return await graph_service.list_triples(db, document_id)


@router.post("/triples", response_model=GraphRelationResponse)
async def create_graph_triple(
    payload: GraphRelationCreate,
    db: AsyncSession = Depends(get_db),
):
    """写入三元组并同步实体表；供测试与后续检索器使用。"""
    # ``object`` 为 Pydantic 字段名，服务层参数用 ``object_`` 避让内置名，
    # 故显式传参而非 ``**model_dump()``。
    return await graph_service.create_triple(
        db,
        document_id=payload.document_id,
        subject=payload.subject,
        relation=payload.relation,
        object_=payload.object,
        chunk_index=payload.chunk_index,
        content=payload.content,
    )
