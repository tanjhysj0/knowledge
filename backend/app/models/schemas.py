from pydantic import BaseModel, ConfigDict
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

    model_config = ConfigDict(from_attributes=True)


class ChatMessageBase(BaseModel):
    role: str
    content: str
    document_ids: Optional[str] = None


class ChatMessageCreate(ChatMessageBase):
    pass


class ChatMessageResponse(ChatMessageBase):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ChatRequest(BaseModel):
    message: str
    document_ids: list[int] = []
    # #35 + #34 集成：发消息时携带会话 id，chat.py 会写入 ``ChatMessage.conversation_id``。
    # reload / 切换会话时通过 ``GET /api/conversations/{id}/messages`` 恢复历史。
    conversation_id: Optional[int] = None


class ChatResponse(BaseModel):
    message: str
    sources: list[str] = []


# ----------------- 会话（#34） -----------------

class ConversationBase(BaseModel):
    """会话 Pydantic 基类，仅作 :class:`ConversationCreate` 的复制定义。"""

    title: Optional[str] = None


class ConversationCreate(ConversationBase):
    """``POST /api/conversations`` 请求体。``title`` 可选；缺省时由后端赋默认值。"""

    pass


class ConversationUpdate(BaseModel):
    """``PATCH /api/conversations/{id}`` 请求体（#35）。

    全部字段可选；未提供的字段保持不变。
    """

    title: Optional[str] = None


class ConversationResponse(ConversationBase):
    """会话响应载荷：返回 id / title / message_count / 时间戳。"""

    id: int
    message_count: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


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


class SettingsUpdateResponse(BaseModel):
    """PUT /api/settings 的响应载荷。"""
    message: str
    settings: SettingsResponse