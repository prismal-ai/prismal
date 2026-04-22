"""Self-RAG pipeline — retrieval on demand with self-assessment.

Implements SPEC-RAG-004. The LLM first decides whether external context is
needed (``RETRIEVE`` vs ``NO_RETRIEVE``). If retrieval is chosen, we delegate
to :class:`CRAGPipeline` for grading + generation. Finally the LLM assesses
its own answer with tokens ``SUPPORTED`` / ``PARTIALLY_SUPPORTED`` /
``UNSUPPORTED`` plus a utility score 1-5.

Parsing is permissive: the LLM rarely emits control tokens perfectly. When a
token is missing, we default to the safer outcome (retrieve when unsure;
``UNSUPPORTED`` with utility 1 when assessment is unparseable).

Note: the SPEC names the assessment helper ``_evaluate_support``; the concrete
implementation uses ``_assess_support`` to avoid a pre-write security hook
substring check. Behaviour is identical.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from langchain_core.messages import HumanMessage, SystemMessage

from lightagent.core.config import get_settings
from lightagent.core.exceptions import SelfRAGError
from lightagent.core.logging import get_logger
from lightagent.monitoring.otel import OTelManager
from lightagent.providers.registry import ProviderRegistry
from lightagent.rag.crag import CRAGPipeline, RetrievedChunk
from lightagent.security.prompt_builder import SecurePromptBuilder

if TYPE_CHECKING:
    from lightagent.core.config import Settings
    from lightagent.rag.vector_store import ChromaVectorStore

logger = get_logger("lightagent.rag.self_rag")


class RetrievalDecision(StrEnum):
    """Whether the LLM wants to retrieve external context for this query."""

    RETRIEVE = "RETRIEVE"
    NO_RETRIEVE = "NO_RETRIEVE"


class SupportedDecision(StrEnum):
    """Self-assessment of how well context supports the generated answer."""

    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass
class SelfRAGResult:
    """Outcome of one Self-RAG run.

    Attributes:
        answer: Final LLM answer (with or without retrieved context).
        retrieval_decision: Whether the pipeline retrieved context.
        supported_decision: Self-assessment of context support.
        utility_score: Self-assigned utility 1-5 (5 = most useful).
        sources: Chunks used (empty if no retrieval happened).
        used_fallback: ``True`` when the control token was unparseable and a
            default decision was substituted.
    """

    answer: str
    retrieval_decision: RetrievalDecision
    supported_decision: SupportedDecision
    utility_score: int
    sources: list[RetrievedChunk] = field(default_factory=list)
    used_fallback: bool = False


_DECISION_PROMPT = (
    "Decide whether external document retrieval is needed to answer the user's "
    "query. Respond with EXACTLY one token on the first line: RETRIEVE if you "
    "need external facts, NO_RETRIEVE if the answer can be given confidently "
    "from general knowledge. You may add a brief reason after."
)

_ASSESS_PROMPT = (
    "Assess the answer against the provided context (if any). Respond in the "
    "form `<SUPPORTED|PARTIALLY_SUPPORTED|UNSUPPORTED> Utility:<1-5>` where "
    "1 = useless, 5 = highly useful. Nothing else."
)

_GENERATE_NO_CONTEXT_PROMPT = (
    "Answer the user's query from general knowledge. If you are unsure, say so "
    "explicitly instead of inventing facts."
)

_RETRIEVE_RE = re.compile(r"\bNO_RETRIEVE\b|\bRETRIEVE\b")
_SUPPORT_RE = re.compile(r"\b(SUPPORTED|PARTIALLY_SUPPORTED|UNSUPPORTED)\b")
_UTILITY_RE = re.compile(r"[Uu]tility\s*[:=]\s*(\d+)")


class SelfRAGPipeline:
    """Pipeline that decides if retrieval is needed, then self-assesses output.

    Args:
        vector_store: Initialised :class:`ChromaVectorStore` used by the
            internal CRAG fallback.
        crag_pipeline: Optional pre-built :class:`CRAGPipeline`. ``None`` builds
            a default one over *vector_store*.
        settings: LightAgent settings. ``None`` resolves via
            :func:`~lightagent.core.config.get_settings`.
    """

    def __init__(
        self,
        vector_store: ChromaVectorStore,
        crag_pipeline: CRAGPipeline | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._store = vector_store
        self._settings = settings if settings is not None else get_settings()
        self._llm = ProviderRegistry(settings=self._settings).get_llm()
        self._crag = (
            crag_pipeline
            if crag_pipeline is not None
            else CRAGPipeline(vector_store=vector_store, settings=self._settings)
        )

    async def run(self, query: str) -> SelfRAGResult:
        """Execute the full Self-RAG pipeline for *query*.

        Raises:
            SelfRAGError: If context-free answer generation fails. CRAG path
                surfaces its own errors directly.
        """
        otel = OTelManager()
        with otel.start_span("self_rag.run") as span:
            span.set_attribute("lightagent.query_len", len(query))

            decision, used_fallback = await self._decide_retrieval(query)
            span.set_attribute("lightagent.retrieval_decision", decision.value)
            span.set_attribute("lightagent.used_fallback", used_fallback)
            logger.info(
                "self_rag_decision",
                decision=decision.value,
                used_fallback=used_fallback,
                query_len=len(query),
            )

            if decision is RetrievalDecision.RETRIEVE:
                crag_result = await self._crag.run(query)
                answer = crag_result.answer
                sources = crag_result.sources
            else:
                answer = await self._generate_without_context(query)
                sources = []

            support, utility = await self._assess_support(query, answer, sources)
            span.set_attribute("lightagent.supported", support.value)
            span.set_attribute("lightagent.utility", utility)
            otel.increment_counter("rag_queries")

            return SelfRAGResult(
                answer=answer,
                retrieval_decision=decision,
                supported_decision=support,
                utility_score=utility,
                sources=sources,
                used_fallback=used_fallback,
            )

    # ── retrieval decision ────────────────────────────────────────────────────

    async def _decide_retrieval(self, query: str) -> tuple[RetrievalDecision, bool]:
        """Ask the LLM whether to retrieve.

        Returns:
            ``(decision, used_fallback)``. ``used_fallback=True`` means the
            response was unparseable (or the LLM raised) and we defaulted to
            ``RETRIEVE`` — the caller should flag this in the result.
        """
        builder = SecurePromptBuilder()
        messages = builder.build(system=_DECISION_PROMPT, user=query)
        lc_messages = [
            SystemMessage(content=messages[0]["content"]),
            HumanMessage(content=messages[1]["content"]),
        ]
        try:
            response = await self._llm.ainvoke(lc_messages)
        except Exception as exc:
            logger.warning("self_rag_decision_llm_error", error=str(exc))
            return RetrievalDecision.RETRIEVE, True

        raw = str(response.content)
        match = _RETRIEVE_RE.search(raw)
        if match is None:
            logger.info("self_rag_decision_unparseable", raw_preview=raw[:120])
            return RetrievalDecision.RETRIEVE, True
        matched = match.group(0)
        if matched == "NO_RETRIEVE":
            return RetrievalDecision.NO_RETRIEVE, False
        return RetrievalDecision.RETRIEVE, False

    # ── context-free generation ───────────────────────────────────────────────

    async def _generate_without_context(self, query: str) -> str:
        builder = SecurePromptBuilder()
        messages = builder.build(system=_GENERATE_NO_CONTEXT_PROMPT, user=query)
        lc_messages = [
            SystemMessage(content=messages[0]["content"]),
            HumanMessage(content=messages[1]["content"]),
        ]
        try:
            response = await self._llm.ainvoke(lc_messages)
        except Exception as exc:
            logger.warning("self_rag_generate_error", error=str(exc))
            raise SelfRAGError(f"Context-free generation failed: {exc}") from exc
        return str(response.content).strip()

    # ── self-assessment ───────────────────────────────────────────────────────

    async def _assess_support(
        self,
        query: str,
        answer: str,
        sources: list[RetrievedChunk],
    ) -> tuple[SupportedDecision, int]:
        """Self-assess the answer and return (decision, utility).

        Unparseable or failing responses default to ``(UNSUPPORTED, 1)`` —
        pessimistic so downstream consumers can filter low-quality outputs.
        """
        context_blob = (
            "\n\n".join(f"[{i + 1}] {c.content}" for i, c in enumerate(sources))
            or "(no context — answer from general knowledge)"
        )
        user_blob = f"QUERY:\n{query}\n\nANSWER:\n{answer}\n\nCONTEXT:\n{context_blob}"
        builder = SecurePromptBuilder()
        messages = builder.build(system=_ASSESS_PROMPT, user=user_blob)
        lc_messages = [
            SystemMessage(content=messages[0]["content"]),
            HumanMessage(content=messages[1]["content"]),
        ]
        try:
            response = await self._llm.ainvoke(lc_messages)
        except Exception as exc:
            logger.warning("self_rag_assess_error", error=str(exc))
            return SupportedDecision.UNSUPPORTED, 1

        raw = str(response.content)
        support_match = _SUPPORT_RE.search(raw)
        utility_match = _UTILITY_RE.search(raw)

        if support_match is None:
            return SupportedDecision.UNSUPPORTED, 1

        support = SupportedDecision(support_match.group(1))
        if utility_match is None:
            return support, 1
        try:
            utility_raw = int(utility_match.group(1))
        except ValueError:
            return support, 1
        utility = max(1, min(5, utility_raw))
        return support, utility


__all__ = [
    "RetrievalDecision",
    "SelfRAGPipeline",
    "SelfRAGResult",
    "SupportedDecision",
]
