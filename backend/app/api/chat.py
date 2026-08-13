"""聊天路由层：仅做 HTTP/SSE 适配、依赖注入和服务调用。

#36：会话上下文隔离 - ``ChatRequest.conversation_id`` 由 Pydantic 强制必填，
``chat_service`` 进一步校验会话存在 (404)；多轮上下文严格按会话 id 过滤，
不再保留全局 ``GET /api/chat/history`` 端点（前端已统一走
``/api/conversations/{id}/messages``）。

#45：LLM 未配置时两个端点都立即拒绝 - 非流式返回 503 JSON，
流式先产 ``event: error``（带 ``reason``）再 ``event: done``。
"""
import json
from typing import AsyncIterator, Dict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.schemas import ChatRequest, ChatResponse
from app.services import chat as chat_service
from app.services.conversations import ConversationNotFoundError
from app.services.llm import is_e2e_mock_request, is_llm_configured

router = APIRouter()


def _llm_unavailable_json(reason: str) -> JSONResponse:
    """``#45`` 503 JSON 响应：``{"reason"}``（前端只读 ``reason``）。"""
    return JSONResponse(
        status_code=503,
        content={"reason": reason},
    )


async def _llm_unavailable_events(reason: str) -> AsyncIterator[Dict[str, str]]:
    """``#45`` 流式拒绝事件：先 ``error``（带 ``reason``）再 ``done``。

    前端会在首条 ``error`` 事件处中断 SSE 循环，后一条 ``done`` 仅用于兼容
    通用 SSE 客户端的"流结束"语义（标准 SSE 客户端不会读到 ``done``）。
    """
    yield {
        "event": "error",
        "data": json.dumps(
            {"reason": reason, "error": "LLM not configured"},
            ensure_ascii=False,
        ),
    }
    yield {
        "event": "done",
        "data": json.dumps({"sources": []}, ensure_ascii=False),
    }


@router.post("", response_model=ChatResponse)
async def chat(
    request: Request,
    payload: ChatRequest,
    db: AsyncSession = Depends(get_db),
):
    """Non-streaming RAG-based chat endpoint.

    #45：preflight 不通过时直接返回 503，不调 LLM / 不写库。
    E2E mock 请求（X-E2E-Test）由 MockLLMProvider 应答，无需真实配置，跳过 preflight。
    """
    configured, reason = is_llm_configured()
    if not configured and not is_e2e_mock_request(request):
        return _llm_unavailable_json(reason)

    try:
        result = await chat_service.ask(
            question=payload.message,
            document_ids=payload.document_ids,
            conversation_id=payload.conversation_id,
            db=db,
            request=request,
        )
    except ConversationNotFoundError as exc:
        # #36：会话不存在 → 404（之前会写入孤儿消息）
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ChatResponse(message=result["answer"], sources=result["sources"])


@router.post("/stream")
async def chat_stream(
    request: Request,
    payload: ChatRequest,
    db: AsyncSession = Depends(get_db),
):
    """Streaming RAG-based chat with multi-turn context.

    ``conversation_id`` 不存在时 ``chat_service.stream_answer`` 会发出
    单条 ``event: error`` SSE，事件 ``data.error`` 携带 404 信息，前端
    据此提示用户刷新页面。

    #45：preflight 不通过时立即产 ``error`` + ``done`` SSE，不调 LLM / 不写库。
    # E2E mock 请求（X-E2E-Test）由 MockLLMProvider 应答，无需真实配置，跳过 preflight。
    """
    configured, reason = is_llm_configured()
    if not configured and not is_e2e_mock_request(request):
        return EventSourceResponse(_llm_unavailable_events(reason))

    return EventSourceResponse(
        chat_service.stream_answer(
            question=payload.message,
            document_ids=payload.document_ids,
            conversation_id=payload.conversation_id,
            db=db,
            request=request,
        )
    )
