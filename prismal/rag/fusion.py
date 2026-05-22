"""RAG-Fusion — multi-query retrieval with Reciprocal Rank Fusion.

Implements SPEC-RAG-002: generate N reformulations of the query, run N
searches in parallel, then re-rank the union of their results with RRF
(Cormack et al., 2009). Improves recall when a single query phrasing misses
relevant documents.

Example::

    from prismal.rag.fusion import RAGFusionEngine
    from prismal.rag.vector_store import ChromaVectorStore

    store = ChromaVectorStore()
    engine = RAGFusionEngine(vector_store=store, n_queries=4)
    result = await engine.search("LangGraph supervisor", k=5)
    print(result.queries)  # ['LangGraph supervisor', 'hub-and-spoke agents', ...]
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from langchain_core.messages import HumanMessage, SystemMessage

from prismal.core.config import get_settings
from prismal.core.exceptions import FusionError
from prismal.core.logging import get_logger
from prismal.monitoring.otel import OTelManager
from prismal.providers.registry import ProviderRegistry
from prismal.rag.crag import RetrievedChunk
from prismal.security.prompt_builder import SecurePromptBuilder

if TYPE_CHECKING:
    from prismal.core.config import Settings
    from prismal.rag.vector_store import ChromaVectorStore

logger = get_logger("prismal.rag.fusion")

_VARIANT_PROMPT = (
    "You generate alternative phrasings of a search query to improve retrieval "
    "recall. Output exactly {n} numbered variants (1., 2., ...), one per line, "
    "each a complete rephrasing. Do not add commentary, headings, or quotes."
)


@dataclass
class FusionResult:
    """Result of a RAG-Fusion search.

    Attributes:
        chunks: Chunks fused and re-ranked by RRF, highest-scored first.
        queries: The N query phrasings that were searched (original + variants).
        per_query_results: Raw retrieved chunks per query (for debugging).
    """

    chunks: list[RetrievedChunk]
    queries: list[str]
    per_query_results: dict[str, list[RetrievedChunk]]


def reciprocal_rank_fusion(
    ranked_lists: list[list[RetrievedChunk]],
    k: int = 60,
) -> list[RetrievedChunk]:
    """Apply Reciprocal Rank Fusion over multiple ranked lists.

    Formula::

        score(d) = Σ_{q in queries} 1 / (k + rank(d, q))

    Chunks are identified by ``(source, chunk_id)`` — the first occurrence of
    each identity wins (subsequent copies are dropped but still contribute
    their rank to the score).

    Args:
        ranked_lists: List of ranked chunk lists, one per query variant.
        k: Smoothing constant (default 60, from Cormack et al. 2009).

    Returns:
        Deduplicated list of chunks ordered by descending RRF score.
    """
    scores: dict[tuple[str, str], float] = {}
    representative: dict[tuple[str, str], RetrievedChunk] = {}

    for ranked in ranked_lists:
        for rank, chunk in enumerate(ranked, start=1):
            key = (chunk.source, chunk.chunk_id)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            representative.setdefault(key, chunk)

    fused = [representative[key] for key in scores]
    fused.sort(key=lambda c: scores[(c.source, c.chunk_id)], reverse=True)
    return fused


class RAGFusionEngine:
    """Multi-query RAG with Reciprocal Rank Fusion.

    Generates ``n_queries - 1`` variants of the input, runs one search per
    (original + variants) concurrently, and fuses the result lists with RRF.

    Args:
        vector_store: Initialised :class:`ChromaVectorStore`.
        n_queries: Total number of query phrasings, counting the original
            (default 4 — so 3 LLM-generated variants).
        rrf_k: RRF smoothing constant (default 60).
        settings: Prismal settings. ``None`` resolves via
            :func:`~prismal.core.config.get_settings`.
    """

    def __init__(
        self,
        vector_store: ChromaVectorStore,
        n_queries: int = 4,
        rrf_k: int = 60,
        settings: Settings | None = None,
    ) -> None:
        self._store = vector_store
        self._n_queries = n_queries
        self._rrf_k = rrf_k
        self._settings = settings if settings is not None else get_settings()
        self._llm = ProviderRegistry(settings=self._settings).get_llm()

    async def search(self, query: str, k: int = 5) -> FusionResult:
        """Run parallel multi-query search and fuse with RRF.

        Args:
            query: Original user query.
            k: Top-k per individual variant search (pre-fusion).

        Returns:
            :class:`FusionResult` with fused chunks and per-query metadata.

        Raises:
            FusionError: If variant generation or any downstream step fails.
        """
        otel = OTelManager()
        with otel.start_span("fusion.search") as span:
            span.set_attribute("prismal.query_len", len(query))
            span.set_attribute("prismal.n_queries", self._n_queries)
            span.set_attribute("prismal.k", k)

            variants = await self._generate_query_variants(query)
            all_queries = [query, *variants][: self._n_queries]

            per_query_results = await self._run_parallel_searches(all_queries, k)
            ranked_lists = [per_query_results[q] for q in all_queries]
            fused = reciprocal_rank_fusion(ranked_lists, k=self._rrf_k)

            span.set_attribute("prismal.fused_count", len(fused))
            otel.increment_counter("rag_queries")

            logger.info(
                "fusion_search_done",
                query_count=len(all_queries),
                fused_count=len(fused),
            )
            return FusionResult(
                chunks=fused,
                queries=all_queries,
                per_query_results=per_query_results,
            )

    async def _run_parallel_searches(
        self, queries: list[str], k: int
    ) -> dict[str, list[RetrievedChunk]]:
        """Dispatch one similarity search per query concurrently."""

        async def run_one(q: str) -> tuple[str, list[RetrievedChunk]]:
            raw = await asyncio.to_thread(self._store.similarity_search, q, k)
            chunks: list[RetrievedChunk] = []
            for i, (doc, score) in enumerate(raw):
                chunks.append(
                    RetrievedChunk(
                        source=doc.metadata.get("source", "unknown"),
                        chunk_id=doc.metadata.get("chunk_id", str(i)),
                        relevance_score=score,
                        content=doc.page_content,
                    )
                )
            return q, chunks

        pairs = await asyncio.gather(*(run_one(q) for q in queries))
        return dict(pairs)

    async def _generate_query_variants(self, query: str) -> list[str]:
        """Ask the LLM for ``n_queries - 1`` rephrasings of *query*.

        Parses numbered lines ``1. ..., 2. ...`` from the LLM output. If
        parsing yields fewer variants than requested, the available variants
        are returned as-is — the caller must handle short lists gracefully.

        Raises:
            FusionError: If the LLM call fails.
        """
        n_variants = max(0, self._n_queries - 1)
        if n_variants == 0:
            return []

        builder = SecurePromptBuilder()
        messages = builder.build(
            system=_VARIANT_PROMPT.format(n=n_variants),
            user=query,
        )
        lc_messages = [
            SystemMessage(content=messages[0]["content"]),
            HumanMessage(content=messages[1]["content"]),
        ]
        try:
            response = await self._llm.ainvoke(lc_messages)
        except Exception as exc:
            logger.warning("fusion_variant_error", error=str(exc))
            raise FusionError(f"Variant generation failed: {exc}") from exc

        raw = str(response.content)
        variants: list[str] = []
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            match = re.match(r"^\d+[\.\)]\s*(.+)$", stripped)
            if match:
                variants.append(match.group(1).strip())
            elif stripped:
                variants.append(stripped)
        return variants[:n_variants]


__all__ = ["FusionResult", "RAGFusionEngine", "reciprocal_rank_fusion"]
