from typing import List, Dict, Any, AsyncGenerator, Optional

from starlette.requests import Request

from app.services.vector_store import VectorStoreService
from app.services.llm import get_llm_provider
from app.core.config import get_settings

settings = get_settings()

RETRIEVAL_SCORE_THRESHOLD = 0.5


class RAGService:
    """Service for RAG-based question answering. Vector search is disabled (no embedding provider)."""

    def __init__(self, request: Optional[Request] = None):
        self._vector_store = VectorStoreService()
        self._request = request

    def _llm(self):
        """Resolve the current LLM provider on each call so settings changes take effect.

        The ``request`` propagated from the API route is forwarded to
        :func:`get_llm_provider` so the E2E ``X-E2E-Test`` header can swap in
        :class:`MockLLMProvider` without leaking the FastAPI ``Request`` into
        the rest of the business logic.
        """
        return get_llm_provider(self._request)

    def _build_rag_prompt(self, question: str, context_chunks: List[Dict[str, Any]]) -> str:
        if not context_chunks:
            return f"Question: {question}\n\nPlease answer based on your general knowledge."

        context_text = "\n\n".join(
            f"[Document {i+1}]\n{chunk.get('content', '')}"
            for i, chunk in enumerate(context_chunks)
        )

        return f"""Based on the following context, answer the question. If the context doesn't contain relevant information, say so.

Context:
{context_text}

Question: {question}

Answer:"""

    def _build_external_prompt(self, question: str) -> str:
        return f"""Question: {question}

Please answer this question based on your general knowledge."""

    def _search_chunks(self, question: str, document_ids: List[int], top_k: int) -> List[Dict[str, Any]]:
        """Search for relevant chunks. Returns [] when embeddings are unavailable."""
        return []  # Vector search disabled (no embedding provider)

    async def answer(
        self,
        question: str,
        document_ids: List[int] = None,
        top_k: int = 5,
    ) -> Dict[str, Any]:
        """Answer a question using RAG. Returns dict with 'answer', 'sources', 'used_external'."""
        search_results = self._search_chunks(question, document_ids or [], top_k)
        used_external = not search_results

        prompt = (
            self._build_external_prompt(question)
            if used_external
            else self._build_rag_prompt(question, search_results)
        )
        messages = [{"role": "user", "content": prompt}]
        answer_text = await self._llm().chat(messages)

        sources = []
        seen_docs = set()
        for result in search_results:
            doc_id = result.get("document_id")
            if doc_id and doc_id not in seen_docs:
                seen_docs.add(doc_id)
                sources.append(f"doc_{doc_id}")

        return {
            "answer": answer_text,
            "sources": sources,
            "used_external": used_external,
        }

    async def answer_stream(
        self,
        question: str,
        document_ids: List[int] = None,
        top_k: int = 5,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Answer a question with streaming."""
        search_results = self._search_chunks(question, document_ids or [], top_k)
        used_external = not search_results

        prompt = (
            self._build_external_prompt(question)
            if used_external
            else self._build_rag_prompt(question, search_results)
        )
        messages = [{"role": "user", "content": prompt}]

        sources = []
        seen_docs = set()
        for result in search_results:
            doc_id = result.get("document_id")
            if doc_id and doc_id not in seen_docs:
                seen_docs.add(doc_id)
                sources.append(f"doc_{doc_id}")

        try:
            full_answer = ""
            async for chunk in self._llm().stream_chat(messages=messages):
                full_answer += chunk
                yield {"chunk": chunk, "done": False, "sources": sources, "error": None}
            yield {"chunk": "", "done": True, "sources": sources, "error": None}
        except Exception as e:
            yield {"chunk": "", "done": True, "sources": sources, "error": str(e)}