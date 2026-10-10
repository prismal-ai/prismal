"""DEC-SWARM — Skynet worker-output text scorer (Phase DEC — SPEC-DEC-INT-007).

Scores the *text quality* of one worker output for its goal. Completeness and
any numeric checks stay deterministic in ``SkynetSupervisor``; the decision is
advisory (e.g. ranking candidates for ``first_success`` reduction).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prismal.decisions.integrations._common import safe_decide
from prismal.decisions.state import DecisionStateBuilder
from prismal.decisions.types import (
    Decision,
    DecisionKind,
    GatePolicy,
    Outcome,
    QuestionSpec,
    RiskTier,
)

if TYPE_CHECKING:
    from prismal.decisions.cascade import DecisionCascade

INTEGRATION = "swarm"
SWARM_DECISION_ID = "swarm.worker_output"
SWARM_QUESTION = QuestionSpec(
    id=SWARM_DECISION_ID,
    kind=DecisionKind.SCORE,
    instructions="Rate the writing quality of the worker output in the state for the stated goal.",
    criteria="Judge clarity, relevance to the goal and coherence of the text only.",
    levels=("poor", "weak", "adequate", "good", "excellent"),
    risk_tier=RiskTier.LOW,
)


async def score_worker_output(
    goal: str,
    output: str,
    *,
    cascade: DecisionCascade,
    policy: GatePolicy | None = None,
) -> Decision:
    """The cascade's decision (``ESCALATED`` abstention on any failure)."""
    state = (
        DecisionStateBuilder()
        .add_untrusted("goal", goal, source="user")
        .add_untrusted("output", output, source="worker")
        .build()
    )
    d = (
        await safe_decide(
            cascade,
            state,
            {SWARM_DECISION_ID: SWARM_QUESTION},
            policy=policy or GatePolicy(),
            integration=INTEGRATION,
        )
    ).get(SWARM_DECISION_ID)
    if d is None:
        from dataclasses import replace

        return replace(
            Decision.abstain(SWARM_QUESTION, backend="none", model="", reason="backend_error"),
            outcome=Outcome.ESCALATED,
        )
    return d
