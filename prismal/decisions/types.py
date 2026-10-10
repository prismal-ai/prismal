"""Typed decision value objects (Phase DEC — SPEC-DEC-TYP-001/002/003).

A *typed decision* answers a closed question — **Choice** (one of N options),
**Score** (one of 2..10 ordered levels) or **Noul** (yes/no → P(true)) — with a
full probability distribution. ``answer_confidence`` is always derived from the
(calibrated) probabilities; a vendor ``confidence`` field is only ever stored as
``spread_confidence`` (DD-DEC-003).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from prismal.core.exceptions import DecisionBackendError, DecisionConfigError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from prismal.core.config import Settings


class DecisionKind(StrEnum):
    """Shape of the closed question."""

    CHOICE = "choice"  # one of N options (backend caps live in settings)
    SCORE = "score"  # one of 2..10 ordered levels
    NOUL = "noul"  # yes/no -> P(true)


class RiskTier(StrEnum):
    """Stakes of a decision; drives τ, shuffle checks and taint handling."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        """0 (low) .. 3 (critical)."""
        return _TIER_RANK[self]

    def at_least(self, other: RiskTier) -> bool:
        """True when this tier is ``other`` or riskier."""
        return self.rank >= other.rank


_TIER_RANK: dict[RiskTier, int] = {
    RiskTier.LOW: 0,
    RiskTier.MEDIUM: 1,
    RiskTier.HIGH: 2,
    RiskTier.CRITICAL: 3,
}


class DecisionMode(StrEnum):
    """Rollout mode of one integration (ARCHITECTURE §3.5)."""

    OFF = "off"
    SHADOW = "shadow"
    ENFORCE = "enforce"


class Outcome(StrEnum):
    """What the cascade did with a decision."""

    ACCEPTED = "accepted"
    ESCALATED = "escalated"
    ABSTAINED = "abstained"


Reason = Literal[
    "",
    "below_tau",
    "low_margin",
    "shuffle_inconsistent",
    "timeout",
    "backend_error",
    "over_limit",
    "tainted",
    "uncalibrated",
    "model_mismatch",
    "unsupported",
]

_NOUL_LABELS: tuple[str, str] = ("true", "false")
_MIN_LEVELS, _MAX_LEVELS = 2, 10


@dataclass(frozen=True)
class OptionSpec:
    """One Choice option; ``description`` is developer-authored text only (SEC-001)."""

    id: str
    description: str


@dataclass(frozen=True)
class QuestionSpec:
    """A closed question. Every text field is a developer-authored constant (SEC-001)."""

    id: str
    kind: DecisionKind
    instructions: str
    options: tuple[OptionSpec, ...] = ()
    criteria: str = ""
    levels: tuple[str, ...] = ()
    risk_tier: RiskTier = RiskTier.MEDIUM
    language_hint: str | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise DecisionConfigError("QuestionSpec.id must be non-empty")
        if self.kind is DecisionKind.CHOICE:
            ids = [o.id for o in self.options]
            if len(ids) < 2:
                raise DecisionConfigError(f"Choice {self.id!r} needs at least 2 options")
            if len(set(ids)) != len(ids) or not all(ids):
                raise DecisionConfigError(f"Choice {self.id!r} has duplicate or empty option ids")
        elif self.kind is DecisionKind.SCORE:
            n = len(self.levels)
            if not _MIN_LEVELS <= n <= _MAX_LEVELS or len(set(self.levels)) != n:
                raise DecisionConfigError(
                    f"Score {self.id!r} needs {_MIN_LEVELS}..{_MAX_LEVELS} distinct levels, got {n}"
                )

    def labels(self) -> tuple[str, ...]:
        """Answer labels: option ids (Choice), levels (Score) or ``("true", "false")``."""
        if self.kind is DecisionKind.CHOICE:
            return tuple(o.id for o in self.options)
        if self.kind is DecisionKind.SCORE:
            return self.levels
        return _NOUL_LABELS

    def with_options(self, options: tuple[OptionSpec, ...]) -> QuestionSpec:
        """Copy with ``options`` replaced (re-validated)."""
        return replace(self, options=options)

    def schema_hash(self) -> str:
        """sha256 over the canonical JSON of the spec (audit, SPEC-DEC-AUD-001)."""
        canonical = {
            "id": self.id,
            "kind": self.kind.value,
            "instructions": self.instructions,
            "options": [[o.id, o.description] for o in self.options],
            "criteria": self.criteria,
            "levels": list(self.levels),
            "risk_tier": self.risk_tier.value,
            "language_hint": self.language_hint,
        }
        blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Decision:
    """One answered question (SPEC-DEC-TYP-002)."""

    question_id: str
    kind: DecisionKind
    value: str | bool | None
    probabilities: Mapping[str, float]
    answer_confidence: float
    spread_confidence: float
    margin: float
    expected_level: float | None
    model: str
    backend: str
    calibration_version: str | None
    latency_ms: float
    abstained: bool = False
    outcome: Outcome = Outcome.ABSTAINED
    escalated_from: tuple[str, ...] = ()
    reason: Reason = ""

    @classmethod
    def abstain(
        cls,
        question: QuestionSpec,
        *,
        backend: str,
        model: str,
        reason: Reason,
        latency_ms: float = 0.0,
        escalated_from: tuple[str, ...] = (),
    ) -> Decision:
        """An abstention: ``value=None``, zero confidence, ``outcome=ABSTAINED``."""
        return cls(
            question_id=question.id,
            kind=question.kind,
            value=None,
            probabilities=MappingProxyType({}),
            answer_confidence=0.0,
            spread_confidence=0.0,
            margin=0.0,
            expected_level=None,
            model=model,
            backend=backend,
            calibration_version=None,
            latency_ms=latency_ms,
            abstained=True,
            outcome=Outcome.ABSTAINED,
            escalated_from=escalated_from,
            reason=reason,
        )

    @classmethod
    def from_probabilities(
        cls,
        question: QuestionSpec,
        probabilities: Mapping[str, float],
        *,
        backend: str,
        model: str,
        spread_confidence: float = 0.0,
        calibration_version: str | None = None,
        latency_ms: float = 0.0,
    ) -> Decision:
        """Build a pending (``ESCALATED``) decision from a raw distribution.

        Labels outside the question's declared ids/levels, negative mass or an
        empty distribution raise ``DecisionBackendError`` (mitigation M6) — the
        adapter converts that into an abstention.
        """
        probs = _normalise(question, probabilities)
        labels = question.labels()
        if question.kind is DecisionKind.NOUL:
            p_true = probs["true"]
            value: str | bool = p_true >= 0.5
            confidence = max(p_true, 1.0 - p_true)
            margin = abs(2.0 * p_true - 1.0)
        else:
            ranked = sorted(labels, key=lambda lab: (-probs[lab], labels.index(lab)))
            value = ranked[0]
            confidence = probs[ranked[0]]
            margin = confidence - probs[ranked[1]]
        expected: float | None = None
        if question.kind is DecisionKind.SCORE:
            n = len(labels)
            expected = sum(probs[lab] * i for i, lab in enumerate(labels)) / (n - 1)
        return cls(
            question_id=question.id,
            kind=question.kind,
            value=value,
            probabilities=MappingProxyType(probs),
            answer_confidence=confidence,
            spread_confidence=float(spread_confidence),
            margin=margin,
            expected_level=expected,
            model=model,
            backend=backend,
            calibration_version=calibration_version,
            latency_ms=latency_ms,
            abstained=False,
            outcome=Outcome.ESCALATED,
        )


def _normalise(question: QuestionSpec, probabilities: Mapping[str, float]) -> dict[str, float]:
    labels = question.labels()
    raw = {str(k): float(v) for k, v in probabilities.items()}
    if question.kind is DecisionKind.NOUL and set(raw) == {"true"}:
        raw["false"] = 1.0 - raw["true"]
    unknown = set(raw) - set(labels)
    if unknown:
        raise DecisionBackendError(f"labels {sorted(unknown)} not declared by {question.id!r}")
    if any(v < 0.0 or not math.isfinite(v) for v in raw.values()):
        raise DecisionBackendError(f"negative or non-finite probability for {question.id!r}")
    total = sum(raw.values())
    if total <= 0.0:
        raise DecisionBackendError(f"empty distribution for {question.id!r}")
    return {lab: raw.get(lab, 0.0) / total for lab in labels}


def _default_tau() -> dict[RiskTier, float | None]:
    return {
        RiskTier.LOW: 0.85,
        RiskTier.MEDIUM: 0.90,
        RiskTier.HIGH: 0.97,
        RiskTier.CRITICAL: None,
    }


@dataclass(frozen=True)
class GatePolicy:
    """Acceptance thresholds per risk tier (SPEC-DEC-TYP-003)."""

    tau_accept: Mapping[RiskTier, float | None] = field(default_factory=_default_tau)
    min_margin: float = 0.20
    shuffle_check_tiers: frozenset[RiskTier] = frozenset({RiskTier.HIGH, RiskTier.CRITICAL})
    timeout_ms: int = 800
    confirm_floor: float = 0.50

    def tau(self, tier: RiskTier) -> float | None:
        """Acceptance threshold for ``tier``; ``None`` = never auto-accept (CRITICAL)."""
        if tier is RiskTier.CRITICAL:
            return None
        return self.tau_accept.get(tier)

    def with_tau_floor(self, tier: RiskTier, tau: float) -> GatePolicy:
        """Copy whose τ for ``tier`` is ``max(current, tau)`` — raise-only (CAL-001)."""
        current = self.tau(tier)
        if current is None:
            return self
        merged = dict(self.tau_accept)
        merged[tier] = max(current, tau)
        return replace(self, tau_accept=merged)

    @classmethod
    def from_settings(cls, settings: Settings) -> GatePolicy:
        """Defaults raised by ``decision_tau_overrides`` (validated raise-only at load)."""
        policy = cls(
            min_margin=settings.decision_min_margin,
            timeout_ms=settings.decision_timeout_ms,
        )
        for tier_name, tau in settings.decision_tau_overrides.items():
            policy = policy.with_tau_floor(RiskTier(tier_name), tau)
        return policy
