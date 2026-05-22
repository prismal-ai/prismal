"""Adaptive RAG — classifies the query and routes to the best Fase-A engine.

Implements SPEC-RAG-006. The engine chooses between CRAG, HyDE, RAG-Fusion,
Hybrid Search, and Hierarchical RAG based on query shape:

- **FACTUAL_SIMPLE** ("when was X released?") → CRAG (standard path).
- **ABSTRACT** ("why does X use Y?") → HyDE if available, else CRAG.
- **AMBIGUOUS** (short / vague) → RAG-Fusion if available, else CRAG.
- **TECHNICAL** (snake_case, API, SDK, regex) → Hybrid Search if available.
- **CONVERSATIONAL** (refers to prior discussion) → CRAG.
- **MULTI_HOP** (composite: "first ..., then ...") → CRAG fallback (GraphRAG
  not in Fase A; reserved for later).

Classification defaults to a deterministic regex heuristic; an optional
LLM classifier (``use_llm_classifier=True``) is available for edge cases and
falls back to regex on error.

Sync engines (Hybrid, Hierarchical) are dispatched through
:func:`asyncio.to_thread` per SPEC so ``search()`` stays ``async`` regardless
of the underlying engine's API shape.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from langchain_core.messages import HumanMessage, SystemMessage

from prismal.core.config import get_settings
from prismal.core.exceptions import AdaptiveRAGError
from prismal.core.logging import get_logger
from prismal.monitoring.otel import OTelManager
from prismal.providers.registry import ProviderRegistry
from prismal.security.prompt_builder import SecurePromptBuilder

if TYPE_CHECKING:
    from prismal.core.config import Settings
    from prismal.rag.crag import CRAGPipeline, RetrievedChunk
    from prismal.rag.fusion import RAGFusionEngine
    from prismal.rag.hierarchical import HierarchicalRAGEngine
    from prismal.rag.hybrid import HybridSearchEngine
    from prismal.rag.hyde import HyDERetriever

logger = get_logger("lightagent.rag.adaptive")


class QueryType(StrEnum):
    """Classification of a user query for adaptive routing."""

    FACTUAL_SIMPLE = "factual_simple"
    ABSTRACT = "abstract"
    AMBIGUOUS = "ambiguous"
    MULTI_HOP = "multi_hop"
    TECHNICAL = "technical"
    CONVERSATIONAL = "conversational"


@dataclass
class AdaptiveResult:
    """Result of an adaptive search.

    Attributes:
        chunks: Retrieved chunks from the selected engine.
        strategy_used: Name of the engine that actually ran
            (``"crag"`` / ``"hyde"`` / ``"fusion"`` / ``"hybrid"`` /
            ``"hierarchical"``).
        query_type: Classification of the query (may differ from the engine
            chosen, e.g. when the preferred engine is unavailable).
        confidence: Classifier confidence in ``[0.0, 1.0]``.
    """

    chunks: list[RetrievedChunk]
    strategy_used: str
    query_type: QueryType
    confidence: float


# Precompiled regexes shared across all engine instances.
_FACTUAL_RE = re.compile(
    r"\b(when|who|where|which year|how many|how much|cu[aá]ndo|qui[eé]n|d[oó]nde)\b",
    re.IGNORECASE,
)
_ABSTRACT_RE = re.compile(r"\b(why|how come|por qu[eé])\b", re.IGNORECASE)
_MULTI_HOP_RE = re.compile(r"(first .+ then|step by step|and then|paso a paso)", re.IGNORECASE)
_CONVERSATIONAL_RE = re.compile(
    r"\b(we|us|our|earlier|previous|previously|before this|above|discussed|expand on)\b",
    re.IGNORECASE,
)
_TECHNICAL_RE = re.compile(
    r"(\b(API|SDK|CLI|HTTP|JSON|SQL|URL|UUID|JWT)\b|\w+_\w+|\b[a-z]+\.[a-z]+\()"
)
_AMBIGUOUS_VAGUE_RE = re.compile(r"\b(stuff|something|anything|things)\b", re.IGNORECASE)

_VALID_FORCED_STRATEGIES = frozenset({"crag", "hyde", "fusion", "hybrid", "hierarchical"})

_LLM_CLASSIFIER_PROMPT = (
    "Classify the user's query into exactly one category and respond with the "
    "category name only (no explanation): factual_simple, abstract, ambiguous, "
    "multi_hop, technical, conversational."
)


class AdaptiveRAGEngine:
    """Facade that picks the best RAG engine per query.

    Args:
        crag_engine: Required fallback pipeline (always usable).
        hyde_retriever: Optional HyDE retriever (used for ABSTRACT queries).
        fusion_engine: Optional Fusion engine (used for AMBIGUOUS queries).
        hybrid_engine: Optional Hybrid engine (used for TECHNICAL queries).
        hierarchical_engine: Optional Hierarchical engine (reserved for future
            routing — currently not auto-selected, but forceable via
            ``force_strategy="hierarchical"``).
        use_llm_classifier: When ``True``, consult the LLM for classification
            (slower, more nuanced). Defaults to ``False`` (regex-only).
        settings: LightAgent settings. ``None`` resolves via
            :func:`~lightagent.core.config.get_settings`.
    """

    def __init__(
        self,
        crag_engine: CRAGPipeline,
        hyde_retriever: HyDERetriever | None = None,
        fusion_engine: RAGFusionEngine | None = None,
        hybrid_engine: HybridSearchEngine | None = None,
        hierarchical_engine: HierarchicalRAGEngine | None = None,
        use_llm_classifier: bool = False,
        settings: Settings | None = None,
    ) -> None:
        self._crag = crag_engine
        self._hyde = hyde_retriever
        self._fusion = fusion_engine
        self._hybrid = hybrid_engine
        self._hierarchical = hierarchical_engine
        self._use_llm_classifier = use_llm_classifier
        self._settings = settings if settings is not None else get_settings()
        self._llm = (
            ProviderRegistry(settings=self._settings).get_llm() if use_llm_classifier else None
        )

    # ── Public API ────────────────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        k: int = 5,
        force_strategy: str | None = None,
    ) -> AdaptiveResult:
        """Classify *query* and dispatch to the best-fit engine.

        Args:
            query: User query.
            k: Max chunks to return.
            force_strategy: Override the classifier and use a specific engine.
                One of ``"crag"``, ``"hyde"``, ``"fusion"``, ``"hybrid"``,
                ``"hierarchical"``.

        Returns:
            :class:`AdaptiveResult` with chunks, strategy used, classification.

        Raises:
            ValueError: If ``force_strategy`` is not a known name.
            AdaptiveRAGError: If ``force_strategy`` names an engine that is not
                configured on this instance.
        """
        otel = OTelManager()
        with otel.start_span("adaptive.search") as span:
            span.set_attribute("lightagent.query_len", len(query))
            span.set_attribute("lightagent.k", k)

            if force_strategy is not None:
                if force_strategy not in _VALID_FORCED_STRATEGIES:
                    raise ValueError(
                        f"Unknown force_strategy: {force_strategy!r}. "
                        f"Valid: {sorted(_VALID_FORCED_STRATEGIES)}"
                    )
                query_type, confidence = self.classify_query(query)
                chunks = await self._dispatch(force_strategy, query, k)
                strategy = force_strategy
            else:
                if self._use_llm_classifier:
                    query_type, confidence = await self._classify_with_llm(query)
                else:
                    query_type, confidence = self.classify_query(query)
                strategy, chunks = await self._route_and_run(query_type, query, k)

            span.set_attribute("lightagent.query_type", query_type.value)
            span.set_attribute("lightagent.strategy", strategy)
            span.set_attribute("lightagent.num_results", len(chunks))
            otel.increment_counter("rag_queries")

            logger.info(
                "adaptive_search_done",
                query_type=query_type.value,
                strategy=strategy,
                confidence=confidence,
                num_results=len(chunks),
            )
            return AdaptiveResult(
                chunks=chunks,
                strategy_used=strategy,
                query_type=query_type,
                confidence=confidence,
            )

    def classify_query(self, query: str) -> tuple[QueryType, float]:
        """Classify *query* using deterministic regex heuristics.

        The first matching pattern wins; confidence encodes heuristic
        strength (short/vague-match = lower, exact signal = higher).
        """
        stripped = query.strip()
        word_count = len(stripped.split())

        # Multi-hop takes priority — composite phrasing dominates other signals.
        if _MULTI_HOP_RE.search(stripped):
            return QueryType.MULTI_HOP, 0.85
        # References to prior discussion.
        if _CONVERSATIONAL_RE.search(stripped):
            return QueryType.CONVERSATIONAL, 0.75
        # Code-like tokens.
        if _TECHNICAL_RE.search(stripped):
            return QueryType.TECHNICAL, 0.8
        # Why / how-come.
        if _ABSTRACT_RE.search(stripped):
            return QueryType.ABSTRACT, 0.8
        # Vague keywords or very short queries.
        if _AMBIGUOUS_VAGUE_RE.search(stripped) or word_count <= 2:
            return QueryType.AMBIGUOUS, 0.6
        # Wh-factual.
        if _FACTUAL_RE.search(stripped):
            return QueryType.FACTUAL_SIMPLE, 0.8
        # Default: treat as factual with low confidence.
        return QueryType.FACTUAL_SIMPLE, 0.4

    # ── Internal routing ─────────────────────────────────────────────────────

    async def _route_and_run(
        self, query_type: QueryType, query: str, k: int
    ) -> tuple[str, list[RetrievedChunk]]:
        """Map query type to an engine and execute it."""
        preferred: str | None = None
        if query_type is QueryType.ABSTRACT and self._hyde is not None:
            preferred = "hyde"
        elif query_type is QueryType.AMBIGUOUS and self._fusion is not None:
            preferred = "fusion"
        elif query_type is QueryType.TECHNICAL and self._hybrid is not None:
            preferred = "hybrid"
        # FACTUAL_SIMPLE / CONVERSATIONAL / MULTI_HOP / (missing preferred) → CRAG.
        strategy = preferred or "crag"
        chunks = await self._dispatch(strategy, query, k)
        return strategy, chunks

    async def _dispatch(self, strategy: str, query: str, k: int) -> list[RetrievedChunk]:
        """Execute the named engine, bridging sync engines via ``to_thread``."""
        if strategy == "crag":
            crag_result = await self._crag.run(query)
            return crag_result.sources
        if strategy == "hyde":
            if self._hyde is None:
                raise AdaptiveRAGError("HyDERetriever not configured")
            hyde_result = await self._hyde.search(query, k=k)
            return hyde_result.chunks
        if strategy == "fusion":
            if self._fusion is None:
                raise AdaptiveRAGError("RAGFusionEngine not configured")
            fusion_result = await self._fusion.search(query, k=k)
            return fusion_result.chunks
        if strategy == "hybrid":
            if self._hybrid is None:
                raise AdaptiveRAGError("HybridSearchEngine not configured")
            # Hybrid is sync per SPEC; dispatch to a worker thread.
            return await asyncio.to_thread(self._hybrid.search, query, k)
        if strategy == "hierarchical":
            if self._hierarchical is None:
                raise AdaptiveRAGError("HierarchicalRAGEngine not configured")
            # Hierarchical is sync; dispatch to a thread and project parents
            # onto RetrievedChunk so the adaptive result shape stays uniform.
            result = await asyncio.to_thread(self._hierarchical.search, query, k)
            return self._parents_as_chunks(result)
        raise AdaptiveRAGError(f"Unknown strategy: {strategy!r}")

    def _parents_as_chunks(self, hierarchical_result: object) -> list[RetrievedChunk]:
        """Adapt :class:`HierarchicalSearchResult` parents into chunks."""
        from prismal.rag.crag import RetrievedChunk

        parents = getattr(hierarchical_result, "parent_chunks", [])
        return [
            RetrievedChunk(
                source=p.source,
                chunk_id=p.parent_id,
                relevance_score=1.0,
                content=p.content,
            )
            for p in parents
        ]

    # ── Optional LLM classifier ──────────────────────────────────────────────

    async def _classify_with_llm(self, query: str) -> tuple[QueryType, float]:
        """Ask the LLM to classify; fall back to regex on any failure."""
        assert self._llm is not None
        builder = SecurePromptBuilder()
        messages = builder.build(system=_LLM_CLASSIFIER_PROMPT, user=query)
        lc_messages = [
            SystemMessage(content=messages[0]["content"]),
            HumanMessage(content=messages[1]["content"]),
        ]
        try:
            response = await self._llm.ainvoke(lc_messages)
        except Exception as exc:
            logger.warning("adaptive_llm_classify_error", error=str(exc))
            return self.classify_query(query)
        raw = str(response.content).strip().lower()
        for qt in QueryType:
            if qt.value in raw:
                return qt, 0.9
        # LLM produced nothing recognisable → regex fallback.
        return self.classify_query(query)


__all__ = ["AdaptiveRAGEngine", "AdaptiveResult", "QueryType"]
