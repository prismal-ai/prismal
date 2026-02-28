"""CRAG (Corrective RAG) pipeline for the LightAgent RAG system.

Implements SPEC-005: a five-step pipeline that retrieves documents from
ChromaDB, grades each chunk for relevance with an LLM, filters out low-quality
chunks, falls back to a web-search stub when nothing passes the threshold, and
finally generates an answer with source citations.

Pipeline steps
--------------
1. RETRIEVE  — ``ChromaVectorStore.similarity_search(query, k=5)``
2. GRADE     — LLM scores each chunk 0.0-1.0 against the query
3. FILTER    — Keep chunks with ``relevance_score >= 0.5``
4. DECIDE    — If all filtered → web fallback stub; otherwise proceed
5. GENERATE  — LLM answers with context + citation sources

Example::

    from lightagent.rag.crag import CRAGPipeline
    from lightagent.rag.vector_store import ChromaVectorStore

    store = ChromaVectorStore()
    pipeline = CRAGPipeline(vector_store=store)
    result = await pipeline.run("What is LightAgent?")
    print(result.answer)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from langchain_core.messages import HumanMessage

from lightagent.core.config import get_settings
from lightagent.core.logging import get_logger
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from langchain_core.documents import Document

    from lightagent.core.config import Settings
    from lightagent.rag.vector_store import ChromaVectorStore

logger = get_logger("lightagent.rag.crag")

# Relevance threshold: chunks scoring below this are discarded.
_RELEVANCE_THRESHOLD: float = 0.5


# ── Data types ────────────────────────────────────────────────────────────────


@dataclass
class RetrievedChunk:
    """A retrieved and graded document chunk.

    Attributes:
        source: File path or URL where the chunk originated.
        chunk_id: Index or identifier within the source document.
        relevance_score: LLM-assigned relevance score in [0.0, 1.0].
        content: Raw text content of the chunk.
    """

    source: str
    chunk_id: str
    relevance_score: float
    content: str


@dataclass
class CRAGResult:
    """Result produced by the CRAG pipeline.

    Attributes:
        answer: LLM-generated answer incorporating the provided context.
        sources: Chunks that survived filtering (or the web-fallback stub).
        used_web_fallback: ``True`` when the web-search fallback was activated.
    """

    answer: str
    sources: list[RetrievedChunk]
    used_web_fallback: bool


# ── Pipeline ──────────────────────────────────────────────────────────────────


class CRAGPipeline:
    """Corrective RAG pipeline with LLM-based relevance grading.

    Retrieves chunks from ChromaDB, grades them with the configured LLM,
    filters out low-relevance results, and generates a cited answer.  When
    all retrieved chunks are below the relevance threshold a web-search stub
    is injected so the pipeline can still produce an answer.

    Args:
        vector_store: Initialised ``ChromaVectorStore`` to use for retrieval.
        settings: Application settings.  ``None`` resolves via
            :func:`~lightagent.core.config.get_settings`.
    """

    def __init__(
        self,
        vector_store: ChromaVectorStore,
        settings: Settings | None = None,
    ) -> None:
        """Initialise the CRAG pipeline.

        Args:
            vector_store: ChromaDB wrapper used for similarity search.
            settings: LightAgent settings.  ``None`` uses ``get_settings()``.
        """
        self._store = vector_store
        self._settings = settings if settings is not None else get_settings()
        self._llm = ProviderRegistry(settings=self._settings).get_llm()

    # ── Public API ────────────────────────────────────────────────────────────

    async def run(self, query: str) -> CRAGResult:
        """Execute the full CRAG pipeline for *query*.

        Steps:

        1. **RETRIEVE** — similarity search (k=5) against ChromaDB.
        2. **GRADE** — LLM scores each chunk for relevance.
        3. **FILTER** — discard chunks with score < 0.5.
        4. **DECIDE** — activate web-search fallback if nothing passes.
        5. **GENERATE** — produce an answer from the surviving context.

        Args:
            query: User query string.

        Returns:
            :class:`CRAGResult` with the generated answer, cited sources, and
            a flag indicating whether the web-search fallback was used.
        """
        # ── Step 1: RETRIEVE ──────────────────────────────────────────────────
        raw_results: list[tuple[Document, float]] = self._store.similarity_search(
            query, k=5
        )
        logger.info(
            "crag_retrieve",
            query=query,
            retrieved_count=len(raw_results),
        )

        # ── Step 2: GRADE ─────────────────────────────────────────────────────
        graded: list[RetrievedChunk] = []
        for i, (doc, similarity_score) in enumerate(raw_results):
            score = await self._grade_document(
                query=query,
                content=doc.page_content,
                fallback_score=similarity_score,
            )
            chunk = RetrievedChunk(
                source=doc.metadata.get("source", "unknown"),
                chunk_id=doc.metadata.get("chunk_id", str(i)),
                relevance_score=score,
                content=doc.page_content,
            )
            graded.append(chunk)
            logger.info(
                "crag_grade",
                source=chunk.source,
                chunk_id=chunk.chunk_id,
                relevance_score=score,
            )

        # ── Step 3: FILTER ────────────────────────────────────────────────────
        filtered = [c for c in graded if c.relevance_score >= _RELEVANCE_THRESHOLD]
        logger.info(
            "crag_filter",
            total_graded=len(graded),
            passed_filter=len(filtered),
        )

        # ── Step 4: DECIDE ────────────────────────────────────────────────────
        used_web_fallback = False
        if not filtered:
            fallback_chunk = self._web_search_fallback(query)
            filtered = [fallback_chunk]
            used_web_fallback = True
            logger.info("crag_web_fallback_activated", query=query)

        # ── Step 5: GENERATE ──────────────────────────────────────────────────
        logger.info("crag_generate_start", context_chunks=len(filtered))
        answer = await self._generate(query=query, chunks=filtered)

        return CRAGResult(
            answer=answer,
            sources=filtered,
            used_web_fallback=used_web_fallback,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _grade_document(
        self,
        query: str,
        content: str,
        fallback_score: float,
    ) -> float:
        """Ask the LLM to score *content* for relevance to *query*.

        The LLM is prompted to respond with a single float in [0.0, 1.0].
        If the response cannot be parsed as a float, *fallback_score* is used
        instead so the pipeline remains robust to model output variation.

        Args:
            query: The user query to assess relevance against.
            content: The document chunk text to grade.
            fallback_score: Score to use if the LLM response is not parseable.

        Returns:
            A relevance score in [0.0, 1.0].
        """
        prompt = (
            "Score the relevance of this document to the query"
            " on a scale from 0.0 to 1.0.\n"
            f"Query: {query}\n"
            f"Document: {content}\n"
            "Respond with ONLY a float number between 0.0 and 1.0."
        )
        response = await self._llm.ainvoke([HumanMessage(content=prompt)])
        raw_text: str = str(response.content).strip()
        try:
            score = float(raw_text)
        except ValueError:
            logger.info(
                "crag_grade_parse_failure",
                raw_response=raw_text,
                fallback_score=fallback_score,
            )
            score = fallback_score
        return score

    async def _generate(self, query: str, chunks: list[RetrievedChunk]) -> str:
        """Generate an answer from *query* and the filtered *chunks*.

        Builds a context block from all chunks (prefixed with their source
        reference) and passes it along with the query to the LLM.

        Args:
            query: The original user query.
            chunks: Filtered document chunks to use as context.

        Returns:
            The LLM-generated answer string.
        """
        context_parts: list[str] = []
        for i, chunk in enumerate(chunks):
            context_parts.append(
                f"[{i + 1}] Source: {chunk.source}\n{chunk.content}"
            )
        context_block = "\n\n".join(context_parts)

        prompt = (
            "Answer the following query using the provided context. "
            "Include source citations.\n"
            f"Query: {query}\n"
            "Context:\n"
            f"{context_block}\n"
            "Answer:"
        )
        response = await self._llm.ainvoke([HumanMessage(content=prompt)])
        return str(response.content)

    @staticmethod
    def _web_search_fallback(query: str) -> RetrievedChunk:
        """Return a stub web-search fallback chunk for *query*.

        Full web-search integration is deferred to a later phase.  This stub
        ensures the pipeline can always produce an answer when ChromaDB
        retrieval yields no relevant results.

        Args:
            query: The user query for which web search would be performed.

        Returns:
            A :class:`RetrievedChunk` with ``source='web_fallback'``.
        """
        return RetrievedChunk(
            source="web_fallback",
            chunk_id="0",
            relevance_score=0.7,
            content=f"Web search fallback for: {query} (stub implementation)",
        )


__all__ = ["CRAGPipeline", "CRAGResult", "RetrievedChunk"]
