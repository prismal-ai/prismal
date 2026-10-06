"""DEC-CTX — relevance scorer for TKE4 context compaction (Phase DEC — SPEC-DEC-INT-006).

Returns a ``[0, 1]`` score for an accepted Score decision and ``None`` on any
abstention, so the compactor keeps its default policy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prismal.decisions.integrations._common import accepted, resolve_mode, safe_decide
from prismal.decisions.state import DecisionStateBuilder
from prismal.decisions.types import DecisionKind, DecisionMode, GatePolicy, QuestionSpec, RiskTier

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from prismal.core.config import Settings
    from prismal.decisions.cascade import DecisionCascade

INTEGRATION = "ctx"
CTX_DECISION_ID = "ctx.relevance"
CTX_QUESTION = QuestionSpec(
    id=CTX_DECISION_ID,
    kind=DecisionKind.SCORE,
    instructions="How relevant is the observation in the state to the current request?",
    levels=("irrelevant", "marginal", "relevant", "essential"),
    risk_tier=RiskTier.LOW,
)


def make_relevance_scorer(
    cascade: DecisionCascade,
    *,
    settings: Settings | None = None,
    policy: GatePolicy | None = None,
) -> Callable[[str, str], Awaitable[float | None]]:
    """``async (query, text) -> score | None`` for the TKE4 compactor."""
    gate = policy or (GatePolicy.from_settings(settings) if settings else GatePolicy())
    levels = CTX_QUESTION.levels

    async def score(query: str, text: str) -> float | None:
        if resolve_mode(settings, INTEGRATION) is not DecisionMode.ENFORCE:
            return None
        state = (
            DecisionStateBuilder()
            .add_untrusted("request", query, source="user")
            .add_untrusted("observation", text, source="tool")
            .build()
        )
        d = (
            await safe_decide(
                cascade,
                state,
                {CTX_DECISION_ID: CTX_QUESTION},
                policy=gate,
                integration=INTEGRATION,
            )
        ).get(CTX_DECISION_ID)
        if accepted(d) and isinstance(d.value, str) and d.value in levels:
            return levels.index(d.value) / (len(levels) - 1)
        return None

    return score
