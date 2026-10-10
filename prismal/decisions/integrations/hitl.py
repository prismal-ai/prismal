"""DEC-HITL — approval bands (Phase DEC — SPEC-DEC-INT-003, DD-DEC-001).

``auto`` only for an *accepted* "safe to auto-approve" decision; ``confirm`` for
a leaning-yes decision in ``[confirm_floor, τ)``; ``human`` otherwise. An
irreversible or ``CRITICAL`` action always goes to a human — a decision is
never the sole approver of such an action. Off / shadow keep today's behaviour
(the human gate).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from prismal.decisions.integrations._common import (
    accepted,
    launch_shadow,
    resolve_mode,
    safe_decide,
)
from prismal.decisions.state import DecisionStateBuilder
from prismal.decisions.types import DecisionKind, DecisionMode, GatePolicy, QuestionSpec, RiskTier

if TYPE_CHECKING:
    from prismal.core.config import Settings
    from prismal.decisions.cascade import DecisionCascade
    from prismal.decisions.shadow import ShadowRunner

INTEGRATION = "hitl"
HITL_DECISION_ID = "hitl.band"
Band = Literal["auto", "confirm", "human"]


@dataclass(frozen=True)
class GatedAction:
    """An action awaiting approval; ``summary`` is untrusted (agent/user-derived)."""

    name: str
    summary: str
    irreversible: bool = False
    risk_tier: RiskTier = RiskTier.MEDIUM


def build_hitl_question(risk_tier: RiskTier) -> QuestionSpec:
    return QuestionSpec(
        id=HITL_DECISION_ID,
        kind=DecisionKind.NOUL,
        instructions="Can the proposed action in the state be approved without asking a human?",
        criteria=(
            "True only when the action is routine, reversible and clearly within what the "
            "user asked for."
        ),
        risk_tier=risk_tier,
    )


async def hitl_band(
    action: GatedAction,
    *,
    cascade: DecisionCascade,
    settings: Settings | None = None,
    shadow: ShadowRunner | None = None,
    policy: GatePolicy | None = None,
) -> Band:
    """Approval band for ``action``; never ``auto`` for irreversible / critical actions."""
    if action.irreversible or action.risk_tier is RiskTier.CRITICAL:
        return "human"
    mode = resolve_mode(settings, INTEGRATION)
    if mode is DecisionMode.OFF:
        return "human"
    gate = policy or (GatePolicy.from_settings(settings) if settings else GatePolicy())
    question = build_hitl_question(action.risk_tier)
    state = (
        DecisionStateBuilder()
        .add_trusted("action", action.name)
        .add_untrusted("summary", action.summary, source="action")
        .build()
    )
    if mode is DecisionMode.SHADOW:
        launch_shadow(shadow, state, question, gate, INTEGRATION)
        return "human"
    d = (
        await safe_decide(
            cascade, state, {question.id: question}, policy=gate, integration=INTEGRATION
        )
    ).get(question.id)
    if d is None or d.abstained or d.value is None:
        return "human"
    if accepted(d):
        if d.value is not True:
            return "human"
        # Only a calibrated typed decision may auto-approve; an LLM verdict is
        # uncalibrated (pre-DEC semantics) and at most asks for confirmation.
        return "confirm" if d.backend == "llm" else "auto"
    if d.value is True and d.answer_confidence >= gate.confirm_floor:
        return "confirm"
    return "human"
