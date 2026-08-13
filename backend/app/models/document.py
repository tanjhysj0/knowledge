from datetime import datetime
from sqlalchemy import Column, ForeignKey, Integer, String, DateTime, Text
from app.core.database import Base


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(255), nullable=False)
    file_path = Column(String(512), nullable=False)
    file_type = Column(String(50), nullable=False)
    size = Column(Integer, nullable=False)
    chunk_count = Column(Integer, default=0)
    # #47：封面图片相对路径（如 ``covers/123.png``），nullable 保留存量记录兼容。
    # 文件存储于 ``settings.cover_dir``，路径在静态端点白名单内返回。
    cover_image_path = Column(String(512), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Conversation(Base):
    """会话（#34）：聚合一次完整对话。

    :attr:`message_count` 是在消息写入时同步维护的会话级别冗余字段，
    用于列表展示而无需 ``COUNT(*)`` 聚合。
    """

    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=True)
    message_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    role = Column(String(20), nullable=False)  # user or assistant
    content = Column(Text, nullable=False)
    document_ids = Column(String(255), nullable=True)
    # 会话归属（#34）：nullable 以保留历史单会话消息，#36 起在写入路径上强制。
    conversation_id = Column(
        Integer,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    created_at = Column(DateTime, default=datetime.utcnow)
