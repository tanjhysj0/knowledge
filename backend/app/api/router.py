"""统一 API 路由聚合入口。"""
from fastapi import APIRouter

from app.api import (
    conversations,
    covers,
    documents,
    graph,
    health,
    llm_status,
    models,
)
from app.api.v1 import chat as v1_chat
from app.api.v2 import chat as v2_chat


router = APIRouter()


router.include_router(health.router, tags=["health"])
router.include_router(documents.router, prefix="/api/documents", tags=["documents"])
# #47：封面静态资源端点
router.include_router(covers.router, prefix="/api/covers", tags=["covers"])
# #76：聊天端点迁移至 v1（接入层显式传入全量检索策略白名单），旧路径下线
router.include_router(v1_chat.router, prefix="/api/v1/chat", tags=["chat"])
# #77：v2 聊天端点（接入层固定传入子集检索策略白名单），契约与 v1 一致
router.include_router(v2_chat.router, prefix="/api/v2/chat", tags=["chat"])
router.include_router(
    conversations.router, prefix="/api/conversations", tags=["conversations"]
)
router.include_router(models.router, tags=["models"])
router.include_router(llm_status.router, tags=["llm"])
# #80：图谱查询/写入（一跳邻居/按文档查/写入三元组）
router.include_router(graph.router, prefix="/api/graph", tags=["graph"])