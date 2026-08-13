"""统一 API 路由聚合入口。"""
from fastapi import APIRouter

from app.api import (
    chat,
    conversations,
    covers,
    documents,
    health,
    llm_status,
    models,
    settings,
)


router = APIRouter()


router.include_router(health.router, tags=["health"])
router.include_router(documents.router, prefix="/api/documents", tags=["documents"])
# #47：封面静态资源端点
router.include_router(covers.router, prefix="/api/covers", tags=["covers"])
router.include_router(chat.router, prefix="/api/chat", tags=["chat"])
router.include_router(
    conversations.router, prefix="/api/conversations", tags=["conversations"]
)
router.include_router(settings.router, tags=["settings"])
router.include_router(models.router, tags=["models"])
router.include_router(llm_status.router, tags=["llm"])