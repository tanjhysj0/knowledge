from typing import List, Dict, Any, AsyncGenerator
from app.services.embedding import get_embedding_provider
from app.services.vector_store import VectorStoreService
from app.services.llm import LLMService
from app.core.config import get_settings

settings = get_settings()

# Minimum similarity score threshold
RETRIEVAL_SCORE_THRESHOLD = 0.5


class RAGService:
    """Service for RAG-based question answering."""

    def __init__(self):
        self._embedding_service = get_embedding_provider()
        self._vector_store = VectorStoreService()
        self._llm = LLMService()

    def _build_rag_prompt(self, question: str, context_chunks: List[Dict[str, Any]]) -> str:
        """Build RAG prompt from question and retrieved chunks."""
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
        """Build prompt for external knowledge fallback."""
        return f"""Question: {question}

Please answer this question based on your general knowledge."""

    async def answer(
        self,
        question: str,
        document_ids: List[int] = None,
        top_k: int = 5,
    ) -> Dict[str, Any]:
        """
        Answer a question using RAG.

        Returns a dict with 'answer', 'sources', and 'used_external' fields.
        """
        # Generate query embedding
        query_embedding = await self._embedding_service.embed_text(question)

        # Search Milvus for relevant chunks
        search_results = self._vector_store.search(
            query_embedding=query_embedding,
            limit=top_k,
            document_ids=document_ids,
        )

        # Check if retrieval score is above threshold
        avg_score = 0.0
        if search_results:
            scores = [r.get("distance", 0) for r in search_results]
            avg_score = sum(scores) / len(scores)

        used_external = avg_score < RETRIEVAL_SCORE_THRESHOLD

        # Build prompt and generate answer
        if used_external or not search_results:
            prompt = self._build_external_prompt(question)
            messages = [{"role": "user", "content": prompt}]
        else:
            prompt = self._build_rag_prompt(question, search_results)
            messages = [{"role": "user", "content": prompt}]

        answer = await self._llm.chat(messages=messages)

        # Extract source documents from search results
        sources = []
        if search_results:
            seen_docs = set()
            for result in search_results:
                doc_id = result.get("document_id")
                if doc_id and doc_id not in seen_docs:
                    seen_docs.add(doc_id)
                    sources.append(f"doc_{doc_id}")

        return {
            "answer": answer,
            "sources": sources,
            "used_external": used_external,
        }

    async def answer_stream(
        self,
        question: str,
        document_ids: List[int] = None,
        top_k: int = 5,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Answer a question using RAG with streaming.

        Yields dicts with 'chunk', 'done', 'sources', and 'error' fields.
        """
        # Generate query embedding
        query_embedding = await self._embedding_service.embed_text(question)

        # Search Milvus for relevant chunks
        search_results = self._vector_store.search(
            query_embedding=query_embedding,
            limit=top_k,
            document_ids=document_ids,
        )

        # Check if retrieval score is above threshold
        avg_score = 0.0
        if search_results:
            scores = [r.get("distance", 0) for r in search_results]
            avg_score = sum(scores) / len(scores)

        used_external = avg_score < RETRIEVAL_SCORE_THRESHOLD

        # Build prompt and generate answer
        if used_external or not search_results:
            prompt = self._build_external_prompt(question)
            messages = [{"role": "user", "content": prompt}]
        else:
            prompt = self._build_rag_prompt(question, search_results)
            messages = [{"role": "user", "content": prompt}]

        # Extract sources
        sources = []
        if search_results:
            seen_docs = set()
            for result in search_results:
                doc_id = result.get("document_id")
                if doc_id and doc_id not in seen_docs:
                    seen_docs.add(doc_id)
                    sources.append(f"doc_{doc_id}")

        # Stream the response
        try:
            full_answer = ""
            async for chunk in self._llm.stream_chat(messages=messages):
                full_answer += chunk
                yield {
                    "chunk": chunk,
                    "done": False,
                    "sources": sources,
                    "error": None,
                }
            yield {
                "chunk": "",
                "done": True,
                "sources": sources,
                "error": None,
            }
        except Exception as e:
            yield {
                "chunk": "",
                "done": True,
                "sources": sources,
                "error": str(e),
            }
