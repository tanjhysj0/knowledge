"""Unit tests for Pydantic schemas."""
import pytest
from datetime import datetime
from pydantic import ValidationError
from app.models.schemas import (
    DocumentBase,
    DocumentCreate,
    DocumentResponse,
    ChatMessageBase,
    ChatMessageCreate,
    ChatMessageResponse,
    ChatRequest,
    ChatResponse,
    PaginationParams,
    PaginatedDocumentsResponse,
    LLMModelCreate,
    LLMModelResponse,
    LLMModelUpdate,
    LLMSettings,
    SettingsResponse,
    SettingsUpdate,
)


class TestDocumentSchemas:
    """Tests for Document schemas."""

    def test_document_base_valid(self):
        doc = DocumentBase(filename="test.pdf", file_type="pdf", size=1024)
        assert doc.filename == "test.pdf"
        assert doc.file_type == "pdf"
        assert doc.size == 1024

    def test_document_create_valid(self):
        doc = DocumentCreate(
            filename="test.pdf",
            file_type="pdf",
            size=1024,
            file_path="/uploads/test.pdf",
            chunk_count=5,
        )
        assert doc.filename == "test.pdf"
        assert doc.file_path == "/uploads/test.pdf"
        assert doc.chunk_count == 5

    def test_document_create_default_chunk_count(self):
        doc = DocumentCreate(
            filename="test.pdf",
            file_type="pdf",
            size=1024,
            file_path="/uploads/test.pdf",
        )
        assert doc.chunk_count == 0

    def test_document_response_from_attributes(self):
        """Test that DocumentResponse can be created from ORM-like objects."""
        now = datetime.now()
        response = DocumentResponse(
            id=1,
            filename="test.pdf",
            file_type="pdf",
            size=1024,
            file_path="/uploads/test.pdf",
            chunk_count=5,
            created_at=now,
        )
        assert response.id == 1
        assert response.created_at == now

    def test_document_response_cover_image_path_defaults_none(self):
        """#47：未提供 cover_image_path 时默认为 None（存量记录兼容）。"""
        response = DocumentResponse(
            id=1,
            filename="test.txt",
            file_type="txt",
            size=10,
            file_path="/uploads/test.txt",
            chunk_count=1,
            created_at=datetime.now(),
        )
        assert response.cover_image_path is None

    def test_document_response_with_cover_image_path(self):
        """#47：cover_image_path 可被序列化并往返。"""
        response = DocumentResponse(
            id=2,
            filename="test.txt",
            file_type="txt",
            size=10,
            file_path="/uploads/test.txt",
            chunk_count=1,
            created_at=datetime.now(),
            cover_image_path="covers/2.png",
        )
        assert response.cover_image_path == "covers/2.png"

    def test_document_response_validate_from_orm_with_cover(self):
        """#47：model_validate 从 ORM 对象读取 cover_image_path。"""
        now = datetime.now()
        orm_like = type(
            "Doc",
            (),
            {
                "id": 3,
                "filename": "test.txt",
                "file_type": "txt",
                "size": 10,
                "file_path": "/uploads/test.txt",
                "chunk_count": 2,
                "created_at": now,
                "cover_image_path": "covers/3.jpg",
            },
        )()
        response = DocumentResponse.model_validate(orm_like)
        assert response.cover_image_path == "covers/3.jpg"

    def test_document_response_status_progress_default_ready(self):
        """#62：未提供 status/progress 时默认为 ready/100（存量记录兼容）。"""
        response = DocumentResponse(
            id=4,
            filename="test.txt",
            file_type="txt",
            size=10,
            file_path="/uploads/test.txt",
            chunk_count=1,
            created_at=datetime.now(),
        )
        assert response.status == "ready"
        assert response.progress == 100

    def test_document_response_status_progress_serialized(self):
        """#62：status/progress 可被序列化并往返。"""
        response = DocumentResponse(
            id=5,
            filename="test.txt",
            file_type="txt",
            size=10,
            file_path="/uploads/test.txt",
            chunk_count=1,
            created_at=datetime.now(),
            status="processing",
            progress=50,
        )
        assert response.status == "processing"
        assert response.progress == 50

    def test_document_response_rejects_invalid_status(self):
        """#62：status 仅接受 pending/processing/ready/failed 四个枚举值。"""
        with pytest.raises(ValidationError):
            DocumentResponse(
                id=6,
                filename="test.txt",
                file_type="txt",
                size=10,
                file_path="/uploads/test.txt",
                chunk_count=1,
                created_at=datetime.now(),
                status="bogus",
            )

    def test_document_response_progress_out_of_range_rejected(self):
        """#62：progress 仅接受 0-100。"""
        with pytest.raises(ValidationError):
            DocumentResponse(
                id=7,
                filename="test.txt",
                file_type="txt",
                size=10,
                file_path="/uploads/test.txt",
                chunk_count=1,
                created_at=datetime.now(),
                progress=101,
            )


class TestChatSchemas:
    """Tests for Chat schemas."""

    def test_chat_message_base_valid(self):
        msg = ChatMessageBase(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"
        assert msg.document_ids is None

    def test_chat_message_base_with_document_ids(self):
        msg = ChatMessageBase(
            role="user",
            content="Hello",
            document_ids="1,2,3",
        )
        assert msg.document_ids == "1,2,3"

    def test_chat_message_create_valid(self):
        msg = ChatMessageCreate(role="assistant", content="Hi there")
        assert msg.role == "assistant"
        assert msg.content == "Hi there"

    def test_chat_message_response_valid(self):
        now = datetime.now()
        msg = ChatMessageResponse(
            id=1,
            role="user",
            content="Hello",
            document_ids="1",
            created_at=now,
        )
        assert msg.id == 1
        assert msg.created_at == now

    def test_chat_request_valid(self):
        # #36：conversation_id 必填
        req = ChatRequest(message="What is RAG?", conversation_id=1)
        assert req.message == "What is RAG?"
        assert req.document_ids == []
        assert req.conversation_id == 1

    def test_chat_request_with_document_ids(self):
        req = ChatRequest(
            message="Search docs", document_ids=[1, 2, 3], conversation_id=7
        )
        assert req.message == "Search docs"
        assert req.document_ids == [1, 2, 3]
        assert req.conversation_id == 7

    def test_chat_request_missing_conversation_id_rejected(self):
        """#36：未传 ``conversation_id`` 必须 422，由 Pydantic 拦截。"""
        with pytest.raises(ValidationError):
            ChatRequest(message="x", document_ids=[])

    def test_chat_response_valid(self):
        resp = ChatResponse(message="RAG is retrieval-augmented generation")
        assert resp.message == "RAG is retrieval-augmented generation"
        assert resp.sources == []

    def test_chat_response_with_sources(self):
        resp = ChatResponse(
            message="Answer here",
            sources=["doc1.pdf", "doc2.md"],
        )
        assert resp.sources == ["doc1.pdf", "doc2.md"]


class TestPaginationSchemas:
    """Tests for Pagination schemas."""

    def test_pagination_params_defaults(self):
        params = PaginationParams()
        assert params.page == 1
        assert params.page_size == 10

    def test_pagination_params_custom(self):
        params = PaginationParams(page=3, page_size=20)
        assert params.page == 3
        assert params.page_size == 20

    def test_paginated_documents_response_valid(self):
        now = datetime.now()
        docs = [
            DocumentResponse(
                id=i,
                filename=f"doc{i}.pdf",
                file_type="pdf",
                size=100 * i,
                file_path=f"/uploads/doc{i}.pdf",
                chunk_count=i * 2,
                created_at=now,
            )
            for i in range(1, 4)
        ]
        resp = PaginatedDocumentsResponse(
            items=docs,
            total=10,
            page=1,
            page_size=3,
            total_pages=4,
        )
        assert len(resp.items) == 3
        assert resp.total == 10
        assert resp.page == 1
        assert resp.total_pages == 4


class TestLLMModelSchemas:
    """#68：模型列表 schema。"""

    def test_llm_model_create_defaults(self):
        payload = LLMModelCreate(provider_type="openai")
        assert payload.base_url == ""
        assert payload.model_name == ""
        assert payload.api_key == ""
        assert payload.is_default is False

    def test_llm_model_create_rejects_unknown_provider(self):
        with pytest.raises(ValidationError):
            LLMModelCreate(provider_type="azure")

    def test_llm_model_create_rejects_oversized_fields(self):
        """超长字段在 API 层拒绝（422），而不是打到 DB 列长限制（500）。"""
        with pytest.raises(ValidationError):
            LLMModelCreate(provider_type="openai", base_url="x" * 513)
        with pytest.raises(ValidationError):
            LLMModelCreate(provider_type="openai", model_name="x" * 256)
        with pytest.raises(ValidationError):
            LLMModelCreate(provider_type="openai", api_key="x" * 513)

    def test_llm_model_update_all_optional(self):
        payload = LLMModelUpdate()
        assert payload.provider_type is None
        assert payload.api_key is None

    def test_llm_model_update_blank_api_key_allowed(self):
        """api_key 空串合法（语义=保持原值，由服务层解释）。"""
        payload = LLMModelUpdate(api_key="")
        assert payload.api_key == ""

    def test_llm_model_response_roundtrip(self):
        now = datetime(2026, 8, 1)
        response = LLMModelResponse(
            id=1,
            provider_type="openai",
            base_url="https://openai.example",
            model_name="gpt-test",
            api_key_masked="sk-o...1234",
            is_default=True,
            created_at=now,
            updated_at=now,
        )
        assert response.id == 1
        assert response.is_default is True
        assert response.api_key_masked == "sk-o...1234"


class TestSettingsSchemas:
    """Tests for Settings schemas."""

    def test_llm_settings_valid(self):
        settings = LLMSettings(
            provider="openai",
            api_key_masked="sk-***",
            base_url="https://api.openai.com",
            model="gpt-4",
        )
        assert settings.provider == "openai"
        assert settings.api_key_masked == "sk-***"
        assert settings.model == "gpt-4"

    def test_settings_response_valid(self):
        resp = SettingsResponse(
            llm=LLMSettings(
                provider="openai",
                api_key_masked="sk-***",
                base_url="https://api.openai.com",
                model="gpt-4",
            ),
        )
        assert resp.llm.provider == "openai"

    def test_settings_update_partial(self):
        """Test that SettingsUpdate allows partial updates."""
        update = SettingsUpdate(llm_model="gpt-4-turbo")
        assert update.llm_model == "gpt-4-turbo"
        assert update.llm_provider is None

    def test_settings_update_all_fields(self):
        """Test SettingsUpdate with all fields."""
        update = SettingsUpdate(
            llm_provider="anthropic",
            llm_api_key="sk-ant-***",
            llm_base_url="https://api.anthropic.com",
            llm_model="claude-3-opus",
        )
        assert update.llm_provider == "anthropic"
        assert update.llm_model == "claude-3-opus"

    def test_settings_update_empty(self):
        """Test that SettingsUpdate with no fields is valid."""
        update = SettingsUpdate()
        assert update.llm_provider is None
