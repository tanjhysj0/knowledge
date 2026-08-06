"""会话应用服务（#34）。

提供 ``list / create / get / delete`` 与 ``list_messages`` 五个基础操作。
本模块是 :mod:`app.api.conversations` 与 :mod:`app.services.chat`（#36）
共用的数据访问入口；后者会在写入 ``ChatMessage`` 时把 ``conversation_id``
关联上并把 ``message_count / updated_at`` 同步到 :class:`Conversation`。
"""
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import ChatMessage, Conversation


class ConversationServiceError(Exception):
    """会话服务异常基类。"""


class ConversationNotFoundError(ConversationServiceError):
    """请求的会话不存在。"""


_DEFAULT_TITLE = "新对话"


def _normalize_title(title: Optional[str]) -> str:
    """裁剪前后空白，空字符串回落到 ``"新对话"`` 默认值。"""
    if title is None:
        return _DEFAULT_TITLE
    trimmed = title.strip()
    return trimmed or _DEFAULT_TITLE


async def list_conversations(db: AsyncSession) -> List[Conversation]:
    """按 ``updated_at`` 倒序返回全部会话。"""
    result = await db.execute(
        select(Conversation).order_by(Conversation.updated_at.desc())
    )
    return list(result.scalars().all())


async def create_conversation(
    db: AsyncSession, title: Optional[str] = None
) -> Conversation:
    """创建一条新会话。``title`` 缺省或空白时使用 ``"新对话"``。"""
    conv = Conversation(title=_normalize_title(title))
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    return conv


async def get_conversation(db: AsyncSession, conversation_id: int) -> Conversation:
    """按 id 取会话；不存在抛 :class:`ConversationNotFoundError`。"""
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conv = result.scalar_one_or_none()
    if conv is None:
        raise ConversationNotFoundError(f"Conversation {conversation_id} not found")
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
