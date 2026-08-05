from typing import Optional, List, Dict, Any, AsyncGenerator
from openai import AsyncOpenAI
from app.core.config import get_settings

settings = get_settings()


class LLMService:
    """Service for calling OpenAI LLM API."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
        )

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
    ) -> str:
        """Send chat completion request and return the response text."""
        response = await self._client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            temperature=temperature,
        )
        return response.choices[0].message.content or ""

    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
    ) -> AsyncGenerator[str, None]:
        """Send chat completion request with streaming and yield response chunks."""
        stream = await self._client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            temperature=temperature,
            stream=True,
        )
        async for chunk in stream:
            content = chunk.choices[0].delta.content
            if content:
                yield content
