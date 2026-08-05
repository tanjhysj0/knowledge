from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, Text
from app.core.database import Base


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(255), nullable=False)
    file_path = Column(String(512), nullable=False)
    file_type = Column(String(50), nullable=False)
    size = Column(Integer, nullable=False)
    chunk_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    role = Column(String(20), nullable=False)  # user or assistant
    content = Column(Text, nullable=False)
    document_ids = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
