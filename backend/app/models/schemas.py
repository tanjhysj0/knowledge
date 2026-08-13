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
    # #47：封面图片相对路径；存量记录为 None。
    cover_image_path: Optional[str] = None
    # #53：小说名；存量记录为 None，展示层回退 filename。
    title: Optional[str] = None

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
    # #36：会话上下文隔离 - 不传 conversation_id 会被 Pydantic 拒绝 (422)，
    # 传错的 ID 会被 chat.py 校验为 404。多轮上下文严格仅走该会话的消息历史，
    # 不再扫全表。
    conversation_id: int


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


# #45：聊天页 preflight 用的 LLM 可用性状态。
class LLMStatusResponse(BaseModel):
    """``GET /api/llm/status`` 的响应载荷。``reason`` 在 ``configured=True`` 时为空串。"""

    provider: str
    configured: bool
    reason: str
