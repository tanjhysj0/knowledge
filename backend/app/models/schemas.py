from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class DocumentBase(BaseModel):
    filename: str
    file_type: str
    size: int


class DocumentCreate(DocumentBase):
    file_path: str
    chunk_count: int = 0


class DocumentResponse(DocumentBase):
    id: int
    file_path: str
    chunk_count: int
    created_at: datetime

    class Config:
        from_attributes = True


class ChatMessageBase(BaseModel):
    role: str
    content: str
    document_ids: Optional[str] = None


class ChatMessageCreate(ChatMessageBase):
    pass


class ChatMessageResponse(ChatMessageBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class ChatRequest(BaseModel):
    message: str
    document_ids: list[int] = []


class ChatResponse(BaseModel):
    message: str
    sources: list[str] = []
