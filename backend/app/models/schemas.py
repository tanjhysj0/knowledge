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


class PaginationParams(BaseModel):
    page: int = 1
    page_size: int = 10


class PaginatedDocumentsResponse(BaseModel):
    items: list[DocumentResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class LLMSettings(BaseModel):
    provider: str
    api_key_masked: str
    base_url: str
    model: str


class SettingsResponse(BaseModel):
    llm: LLMSettings


class SettingsUpdate(BaseModel):
    llm_provider: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_base_url: Optional[str] = None
    llm_model: Optional[str] = None