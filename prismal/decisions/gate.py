"""Gate evaluation and option-order shuffle (Phase DEC — SPEC-DEC-CAS-002/003).

Pure functions, never raise. A decision is accepted only when every guard
passes; any doubt maps to a rejection *reason* so the cascade escalates
(DD-DEC-002 — no failure path ever maps to "allow").
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from prismal.decisions.types import DecisionKind, RiskTier

if TYPE_CHECKING:
    from prismal.decisions.types import Decision, GatePolicy, QuestionSpec, Reason


def shuffle_required(q: QuestionSpec, policy: GatePolicy) -> bool:
    """CAS-003: high-stakes Choice questions need an option-order consistency check."""
    return q.kind is DecisionKind.CHOICE and q.risk_tier in policy.shuffle_check_tiers


def evaluate_gate(
    d: Decision,
    q: QuestionSpec,
    policy: GatePolicy,
    *,
    calibrated: bool,
    tainted: bool,
    corroborated: bool = False,
    shuffle_consistent: bool | None = None,
) -> tuple[bool, Reason]:
    """Return ``(accept, reason)`` for a non-terminal tier's decision.

    Accept iff not abstained ∧ calibrated ∧ τ(tier) defined ∧ ¬(tainted ∧
    ¬corroborated for ≥ MEDIUM) ∧ confidence ≥ τ ∧ margin ≥ min_margin ∧
    shuffle-consistent where required.
    """
    try:
        if d.abstained or d.value is None:
            return False, d.reason or "backend_error"
        if not calibrated:
            return False, "uncalibrated"
        tau = policy.tau(q.risk_tier)
        if tau is None:
            return False, "below_tau"
        if tainted and not corroborated and q.risk_tier.at_least(RiskTier.MEDIUM):
            return False, "tainted"
        if not d.answer_confidence >= tau:  # ``not >=`` also rejects NaN
            return False, "below_tau"
        if not d.margin >= policy.min_margin:
            return False, "low_margin"
        if shuffle_required(q, policy) and shuffle_consistent is not True:
            return False, "shuffle_inconsistent"
    except Exception:
        return False, "backend_error"
    return True, ""


def shuffle_options(q: QuestionSpec, *, seed: int) -> QuestionSpec:
    """Deterministic *derangement* of a Choice's options: no option keeps its position.

    A model biased toward any fixed position (first, last, …) therefore answers a
    different option id on the permuted request and fails the consistency check.
    """
    if q.kind is not DecisionKind.CHOICE:
        return q
    original = list(q.options)
    rng = random.Random(seed)  # noqa: S311 # nosec B311 — order permutation, not security
    for _ in range(32):
        options = original[:]
        rng.shuffle(options)
        if all(a.id != b.id for a, b in zip(options, original, strict=True)):
            return q.with_options(tuple(options))
    return q.with_options(tuple(original[1:] + original[:1]))  # rotation is a derangement
