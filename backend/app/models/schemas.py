from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime
from typing import Literal, Optional


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
    # #62/#63：处理状态与进度（0-100）；存量记录为 ready/100。
    status: Literal["pending", "processing", "ready", "failed"] = "ready"
    progress: int = Field(default=100, ge=0, le=100)
    # #63：索引处理失败原因（status=failed 时写入）；成功/存量记录为 None。
    error_message: Optional[str] = None

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
    """``POST /api/conversations`` 请求体。``title`` 可选；缺省时由后端赋默认值。

    #52：``document_id`` 可选——提供时按 (client_id, document_id) 幂等
    返回既有绑定会话（不存在则新建并绑定），重复点击同一小说卡片
    不会另开新会话。
    """

    document_id: Optional[int] = None


class ConversationUpdate(BaseModel):
    """``PATCH /api/conversations/{id}`` 请求体（#35）。

    全部字段可选；未提供的字段保持不变。
    """

    title: Optional[str] = None


class ConversationResponse(ConversationBase):
    """会话响应载荷：返回 id / title / message_count / 时间戳。

    #52：透出 ``client_id``（会话归属客户端）与 ``document_id``
    （绑定小说 id，null = 通用会话），前端据此恢复绑定会话。
    """

    id: int
    client_id: str
    document_id: Optional[int] = None
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


# ----------------- 模型列表（#68） -----------------

class LLMModelCreate(BaseModel):
    """``POST /api/models`` 请求体。列表为空时 ``is_default`` 必须为 true。"""

    provider_type: Literal["openai", "anthropic"]
    base_url: str = Field(default="", max_length=512)
    model_name: str = Field(default="", max_length=255)
    api_key: str = Field(default="", max_length=512)
    is_default: bool = False


class LLMModelUpdate(BaseModel):
    """``PUT /api/models/{id}`` 请求体。

    全部字段可选；``api_key`` 留空（缺省或空串）表示保持原值。
    默认切换不在此端点（走 ``PUT /api/models/{id}/default``）。
    """

    provider_type: Optional[Literal["openai", "anthropic"]] = None
    base_url: Optional[str] = Field(default=None, max_length=512)
    model_name: Optional[str] = Field(default=None, max_length=255)
    api_key: Optional[str] = Field(default=None, max_length=512)


class LLMModelResponse(BaseModel):
    """模型记录响应：``api_key`` 脱敏为 ``api_key_masked``。"""

    id: int
    provider_type: str
    base_url: str
    model_name: str
    api_key_masked: str
    is_default: bool
    created_at: datetime
    updated_at: datetime


# ----------------- 模型列表拉取（#69） -----------------

class ModelListFetchRequest(BaseModel):
    """``POST /api/models/fetch`` 请求体：后端代理拉取 provider 模型列表。

    ``api_key`` 仅透传给上游 provider，不落库、不在任何响应中回传。
    """

    provider_type: Literal["openai", "anthropic"]
    base_url: str = Field(default="", max_length=512)
    api_key: str = Field(default="", max_length=512)


class ModelListResponse(BaseModel):
    """``POST /api/models/fetch`` 响应：provider 返回的模型名列表。"""

    models: list[str]


# #45：聊天页 preflight 用的 LLM 可用性状态。
class LLMStatusResponse(BaseModel):
    """``GET /api/llm/status`` 的响应载荷。``reason`` 在 ``configured=True`` 时为空串。"""

    provider: str
    configured: bool
    reason: str
