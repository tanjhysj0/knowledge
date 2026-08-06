"""统一 API 路由聚合入口。"""
from fastapi import APIRouter

from app.api import chat, conversations, documents, health, settings


router = APIRouter()


router.include_router(health.router, tags=["health"])
router.include_router(documents.router, prefix="/api/documents", tags=["documents"])
router.include_router(chat.router, prefix="/api/chat", tags=["chat"])
router.include_router(
    conversations.router, prefix="/api/conversations", tags=["conversations"]
)
router.include_router(settings.router, tags=["settings"])
