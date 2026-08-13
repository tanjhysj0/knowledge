from datetime import datetime
from sqlalchemy import Column, ForeignKey, Index, Integer, String, DateTime, Text, text
from app.core.database import Base


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(255), nullable=False)
    # #53：小说名（管理端表单必填；API 缺省时回退文件名去扩展名）。
    # nullable 保留存量记录兼容，展示层回退 ``filename``。
    title = Column(String(255), nullable=True)
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

    #52：``client_id`` 标识会话所属的浏览器（客户端）空间，
    ``document_id`` 为可选的小说绑定（null = 未绑定的通用会话；
    删除小说不级联清理绑定会话）。

    :attr:`message_count` 是在消息写入时同步维护的会话级别冗余字段，
    用于列表展示而无需 ``COUNT(*)`` 聚合。
    """

    __tablename__ = "conversations"

    # #52：同一客户端下 (client_id, document_id) 唯一，为
    # get_or_create 的幂等提供数据库级兜底（防并发重复绑定）；
    # document_id 为 NULL 的通用会话不受约束。
    __table_args__ = (
        Index(
            "uq_conversations_client_document",
            "client_id",
            "document_id",
            unique=True,
            postgresql_where=text("document_id IS NOT NULL"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    # #52：会话归属的客户端标识（前端首次访问生成的 client key，
    # 经 ``X-Client-Id`` 请求头透传；存量行由内联迁移补齐为 'default'）。
    client_id = Column(String(64), nullable=False, index=True)
    # #52：绑定的小说 id；null = 未绑定小说的通用会话。
    document_id = Column(Integer, nullable=True, index=True)
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
