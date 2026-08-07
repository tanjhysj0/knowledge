"""会话路由层（#34）：纯 HTTP 适配 + 服务调用。"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.schemas import (
    ChatMessageResponse,
    ConversationCreate,
    ConversationResponse,
    ConversationUpdate,
)
from app.services import conversations as conversation_service

router = APIRouter()


@router.get("", response_model=List[ConversationResponse])
async def list_conversations(db: AsyncSession = Depends(get_db)):
    """按 ``updated_at`` 倒序返回全部会话。"""
    items = await conversation_service.list_conversations(db)
    return items


@router.post("", response_model=ConversationResponse)
async def create_conversation(
    payload: ConversationCreate,
    db: AsyncSession = Depends(get_db),
):
    """创建新会话；``title`` 缺省回落到 ``"新对话"``。"""
    conv = await conversation_service.create_conversation(
        db=db, title=payload.title
    )
    return conv


@router.delete("/{conversation_id}")
async def delete_conversation(
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
):
    """删除会话；其下消息随外键 ``ON DELETE CASCADE`` 一并清理。"""
    try:
        await conversation_service.delete_conversation(
            db=db, conversation_id=conversation_id
        )
    except conversation_service.ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"message": "Conversation deleted"}


@router.patch("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: int,
    payload: ConversationUpdate,
    db: AsyncSession = Depends(get_db),
):
    """更新会话属性（当前仅支持 ``title``，#35 使用）。"""
    try:
        conv = await conversation_service.update_conversation(
            db=db, conversation_id=conversation_id, title=payload.title
        )
    except conversation_service.ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return conv


@router.get(
    "/{conversation_id}/messages",
    response_model=List[ChatMessageResponse],
)
async def list_conversation_messages(
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
):
    """返回指定会话的全部消息，按 ``created_at`` 升序。"""
    try:
        items = await conversation_service.list_messages(
            db=db, conversation_id=conversation_id
        )
    except conversation_service.ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return items
