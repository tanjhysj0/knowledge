"""会话应用服务（#34）。

提供 ``list / create / get_or_create / get / delete`` 与 ``list_messages``
等基础操作。本模块是 :mod:`app.api.conversations` 与 :mod:`app.services.chat`
（#36）共用的数据访问入口；后者会在写入 ``ChatMessage`` 时把
``conversation_id`` 关联上并把 ``message_count / updated_at`` 同步到
:class:`Conversation`。

#52：会话按 ``client_id`` 划分客户端（浏览器）空间；``document_id``
非空时标识该会话绑定的小说，同一客户端下 (client_id, document_id)
幂等复用同一条绑定会话。
"""
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import ChatMessage, Conversation


class ConversationServiceError(Exception):
    """会话服务异常基类。"""


class ConversationNotFoundError(ConversationServiceError):
    """请求的会话不存在。"""


_DEFAULT_TITLE = "新对话"

# #52：``X-Client-Id`` 缺失时的回退客户端标识，与内联迁移
# 补齐存量会话的 DEFAULT 'default' 保持一致。
DEFAULT_CLIENT_ID = "default"


def _normalize_title(title: Optional[str]) -> str:
    """裁剪前后空白，空字符串回落到 ``"新对话"`` 默认值。"""
    if title is None:
        return _DEFAULT_TITLE
    trimmed = title.strip()
    return trimmed or _DEFAULT_TITLE


async def list_conversations(
    db: AsyncSession, client_id: Optional[str] = None
) -> List[Conversation]:
    """按 ``updated_at`` 倒序返回会话。

    #52：``client_id`` 提供时仅返回该客户端的会话（跨浏览器隔离）；
    ``None`` 时不过滤——测试清理与存量调用需要全量视图。
    """
    stmt = select(Conversation).order_by(Conversation.updated_at.desc())
    if client_id is not None:
        stmt = stmt.where(Conversation.client_id == client_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def create_conversation(
    db: AsyncSession,
    title: Optional[str] = None,
    *,
    client_id: str = DEFAULT_CLIENT_ID,
    document_id: Optional[int] = None,
) -> Conversation:
    """创建一条新会话。``title`` 缺省或空白时使用 ``"新对话"``。

    #52：``client_id`` 归属指定客户端空间；``document_id`` 非空则绑定小说。
    """
    conv = Conversation(
        client_id=client_id,
        document_id=document_id,
        title=_normalize_title(title),
    )
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    return conv


async def get_or_create_conversation(
    db: AsyncSession,
    *,
    client_id: str,
    title: Optional[str] = None,
    document_id: Optional[int] = None,
) -> Conversation:
    """#52：按 (client_id, document_id) 幂等返回绑定会话。

    ``document_id`` 为 ``None`` 时等价于普通创建；非空时先查该客户端
    下是否已有绑定该小说的会话——有则直接返回（重复点击同一小说卡片
    恢复到既有会话），无则新建并绑定。
    """
    if document_id is not None:
        result = await db.execute(
            select(Conversation).where(
                Conversation.client_id == client_id,
                Conversation.document_id == document_id,
            )
        )
        existing = result.scalars().first()
        if existing is not None:
            return existing
    try:
        return await create_conversation(
            db, title=title, client_id=client_id, document_id=document_id
        )
    except IntegrityError:
        # 并发竞态：唯一索引兜底，另一并发请求已插入绑定会话；
        # 回滚后重查返回既有行（幂等语义，见 #52）。
        if document_id is None:
            raise
        await db.rollback()
        result = await db.execute(
            select(Conversation).where(
                Conversation.client_id == client_id,
                Conversation.document_id == document_id,
            )
        )
        existing = result.scalars().first()
        if existing is None:
            raise
        return existing


async def get_conversation(db: AsyncSession, conversation_id: int) -> Conversation:
    """按 id 取会话；不存在抛 :class:`ConversationNotFoundError`。"""
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conv = result.scalar_one_or_none()
    if conv is None:
        raise ConversationNotFoundError(f"Conversation {conversation_id} not found")
    return conv


async def update_conversation(
    db: AsyncSession, conversation_id: int, title: Optional[str]
) -> Conversation:
    """更新会话属性（当前仅支持 ``title``，#35 使用）。

    ``title`` 为 ``None`` 时视为不修改（保留现状），空字符串 / 纯空白
    则回落到 :data:`_DEFAULT_TITLE`。
    """
    conv = await get_conversation(db, conversation_id)
    if title is not None:
        conv.title = _normalize_title(title)
    await db.commit()
    await db.refresh(conv)
    return conv


async def delete_conversation(db: AsyncSession, conversation_id: int) -> None:
    """删除会话本身；其下消息由 ``ChatMessage.conversation_id`` 的
    ``ON DELETE CASCADE`` 外键约定清理（SQLAlchemy 需要 DB 支持）。
    """
    conv = await get_conversation(db, conversation_id)
    await db.delete(conv)
    await db.commit()


async def list_messages(
    db: AsyncSession, conversation_id: int
) -> List[ChatMessage]:
    """按 ``created_at`` 升序返回该会话的全部消息。"""
    # 校验会话存在，不存在直接抛 404（比返回空数组更明确）。
    await get_conversation(db, conversation_id)
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at.asc())
    )
    return list(result.scalars().all())


async def touch_conversation(
    db: AsyncSession, conversation_id: int, *, delta: int = 1
) -> None:
    """在追加消息后由 :mod:`app.services.chat` 调用以更新 ``updated_at`` /
    ``message_count``，并 ``commit``。
    """
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conv = result.scalar_one_or_none()
    if conv is None:
        # 调用方应保证 conversation_id 存在；不存在时静默忽略以避免误删
        return
    conv.message_count = (conv.message_count or 0) + delta
    conv.updated_at = conv.updated_at  # 触发 onupdate 重写
    await db.commit()
