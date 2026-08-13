"""会话路由层（#34）：纯 HTTP 适配 + 服务调用。

#52：会话空间按客户端隔离——``X-Client-Id`` 请求头决定会话归属的
client_id；GET 列表按其过滤，POST 带 ``document_id`` 时按
(client_id, document_id) 幂等返回绑定会话。
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
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


def _client_id_from(request: Request) -> str | None:
    """#52：从 ``X-Client-Id`` 头取客户端标识；缺失返回 ``None``（回退/不过滤）。"""
    return request.headers.get("X-Client-Id")


@router.get("", response_model=List[ConversationResponse])
async def list_conversations(
    request: Request, db: AsyncSession = Depends(get_db)
):
    """按 ``updated_at`` 倒序返回会话。

    #52：携带 ``X-Client-Id`` 时仅返回该客户端的会话（跨浏览器不可见）；
    缺失时返回全量（存量调用与测试清理的兼容视图）。
    """
    items = await conversation_service.list_conversations(
        db, client_id=_client_id_from(request)
    )
    return items


@router.post("", response_model=ConversationResponse)
async def create_conversation(
    request: Request,
    payload: ConversationCreate,
    db: AsyncSession = Depends(get_db),
):
    """创建新会话；``title`` 缺省回落到 ``"新对话"``。

    #52：``document_id`` 提供时按 (client_id, document_id) 幂等返回
    既有绑定会话（重复点击同一小说卡片不会另开新会话）；``client_id``
    取自 ``X-Client-Id`` 头，缺失回退默认客户端。
    """
    conv = await conversation_service.get_or_create_conversation(
        db=db,
        client_id=_client_id_from(request)
        or conversation_service.DEFAULT_CLIENT_ID,
        title=payload.title,
        document_id=payload.document_id,
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
