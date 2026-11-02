"""DEC-RAG — CRAG chunk relevance (Phase DEC — SPEC-DEC-INT-005).

One Noul per chunk; an accepted decision grades the chunk, an abstention falls
back to the CRAG LLM grader *for that chunk only* (or ``None`` without one).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prismal.decisions.integrations._common import (
    ShadowHandle,
    accepted,
    active_runtime,
    launch_shadow,
    resolve_mode,
    safe_decide,
)
from prismal.decisions.state import DecisionStateBuilder
from prismal.decisions.types import DecisionKind, DecisionMode, GatePolicy, QuestionSpec, RiskTier

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from prismal.core.config import Settings
    from prismal.decisions.cascade import DecisionCascade
    from prismal.decisions.shadow import ShadowRunner
    from prismal.decisions.state import DecisionState

INTEGRATION = "rag"
RAG_DECISION_ID = "rag.chunk_relevance"
RAG_QUESTION = QuestionSpec(
    id=RAG_DECISION_ID,
    kind=DecisionKind.NOUL,
    instructions="Is the chunk in the state relevant to the query in the state?",
    criteria="Relevant means the chunk contains information that helps answer the query.",
    risk_tier=RiskTier.LOW,
)


def _content(chunk: Any) -> str:
    return str(getattr(chunk, "page_content", getattr(chunk, "content", chunk)))


def build_rag_state(query: str, content: str) -> DecisionState:
    """The decision state graded for one chunk: query and chunk, both untrusted."""
    return (
        DecisionStateBuilder()
        .add_untrusted("query", query, source="user")
        .add_untrusted("chunk", content, source="rag")
        .build()
    )


async def grade_chunks(
    query: str,
    chunks: Sequence[Any],
    *,
    cascade: DecisionCascade,
    settings: Settings | None = None,
    fallback: Callable[[str, str], Awaitable[bool]] | None = None,
    shadow: ShadowRunner | None = None,
    policy: GatePolicy | None = None,
) -> list[bool | None]:
    """Relevance per chunk: decided, LLM-fallback graded, or ``None`` (caller grades)."""
    mode = resolve_mode(settings, INTEGRATION)
    if mode is DecisionMode.OFF:
        return [None] * len(chunks)
    gate = policy or (GatePolicy.from_settings(settings) if settings else GatePolicy())
    out: list[bool | None] = []
    for chunk in chunks:
        content = _content(chunk)
        state = build_rag_state(query, content)
        if mode is DecisionMode.SHADOW:
            launch_shadow(shadow, state, RAG_QUESTION, gate, INTEGRATION)
            out.append(None)
            continue
        d = (
            await safe_decide(
                cascade,
                state,
                {RAG_DECISION_ID: RAG_QUESTION},
                policy=gate,
                integration=INTEGRATION,
            )
        ).get(RAG_DECISION_ID)
        if accepted(d) and isinstance(d.value, bool):
            out.append(d.value)
        elif fallback is not None:
            out.append(bool(await fallback(query, content)))
        else:
            out.append(None)
    return out


class CandidateDecisions:
    """Per-candidate pre-grades for ``CRAGPipeline`` (+ shadow handles to record)."""

    def __init__(
        self,
        scores: list[float | None],
        handles: list[ShadowHandle | None] | None = None,
    ) -> None:
        self.scores = scores
        self._handles = handles or [None] * len(scores)

    def score(self, index: int) -> float | None:
        """Pre-grade of candidate *index*, or ``None``."""
        return self.scores[index] if index < len(self.scores) else None

    def record(self, index: int, relevant: bool) -> None:
        """Record the LLM grader's verdict as the shadow reference (no-op otherwise)."""
        handle = self._handles[index] if index < len(self._handles) else None
        if handle is not None:
            handle.record_reference(relevant)


_NO_DECISIONS = CandidateDecisions([])


async def maybe_grade_candidates(
    query: str, contents: Sequence[str], settings: Settings
) -> CandidateDecisions:
    """CRAG seam: no-op unless ``decisions_enabled`` + ``decision_rag_relevance_enabled``."""
    mode, runtime = active_runtime(settings, INTEGRATION)
    if runtime is None:
        return _NO_DECISIONS
    if mode is DecisionMode.ENFORCE:
        graded = await grade_chunks(
            query, contents, cascade=runtime.cascade, settings=settings, policy=runtime.policy
        )
        return CandidateDecisions([None if g is None else (1.0 if g else 0.0) for g in graded])
    handles: list[ShadowHandle | None] = []
    for content in contents:
        state = build_rag_state(query, content)
        handles.append(
            launch_shadow(runtime.shadow, state, RAG_QUESTION, runtime.policy, INTEGRATION)
        )
    return CandidateDecisions([None] * len(contents), handles)
