"""聊天路由层共享的 HTTP/SSE 适配工具。

#77：v1 / v2 聊天端点共用 LLM 未配置（503）的拒绝形状，避免错误契约
实现分叉——非流式返回 503 JSON，流式先产 ``event: error``（带 ``reason``）
再 ``event: done``。逻辑源自 #45，行为与迁移前一致。
"""
import json
from typing import AsyncIterator, Dict

from fastapi.responses import JSONResponse


def llm_unavailable_json(reason: str) -> JSONResponse:
    """``#45`` 503 JSON 响应：``{"reason"}``（前端只读 ``reason``）。"""
    return JSONResponse(
        status_code=503,
        content={"reason": reason},
    )


async def llm_unavailable_events(reason: str) -> AsyncIterator[Dict[str, str]]:
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
