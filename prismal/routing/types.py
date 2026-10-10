"""Routing value objects (Phase AMR — SPEC-AMR-TYP-001).

Tiers are compared only through :data:`TIER_ORDER`. ``ModelTier.AUTO`` (a
gateway-native auto router such as ``openrouter/auto``) is **terminal**: it is
not part of the order, never escalates, never downgrades and is never a batch
candidate (SPEC-AMR-GW-001).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from prismal.decisions.types import RiskTier

if TYPE_CHECKING:
    from prismal.budget.types import Usage


class ModelTier(StrEnum):
    """Capability/cost band of a model."""

    NANO = "nano"
    SMALL = "small"
    MEDIUM = "medium"
    FRONTIER = "frontier"
    AUTO = "auto"


TIER_ORDER: tuple[ModelTier, ...] = (
    ModelTier.NANO,
    ModelTier.SMALL,
    ModelTier.MEDIUM,
    ModelTier.FRONTIER,
)


def tier_index(tier: ModelTier) -> int:
    """Position of *tier* in :data:`TIER_ORDER`; ``AUTO`` has none (ValueError)."""
    if tier is ModelTier.AUTO:
        raise ValueError("ModelTier.auto is terminal and has no position in TIER_ORDER")
    return TIER_ORDER.index(tier)


def tier_at_least(tier: ModelTier, floor: ModelTier) -> bool:
    """True when *tier* is *floor* or more capable (both ordered tiers)."""
    return tier_index(tier) >= tier_index(floor)


def tier_shift(tier: ModelTier, steps: int) -> ModelTier:
    """Move *steps* along :data:`TIER_ORDER`, clamped at both ends; ``AUTO`` is fixed."""
    if tier is ModelTier.AUTO:
        return tier
    idx = min(max(tier_index(tier) + steps, 0), len(TIER_ORDER) - 1)
    return TIER_ORDER[idx]


def max_tier(a: ModelTier, b: ModelTier) -> ModelTier:
    """The more capable of two ordered tiers."""
    return a if tier_index(a) >= tier_index(b) else b


@dataclass(frozen=True)
class TaskFeatures:
    """Deterministic, LLM-free description of one agent call."""

    agent: str
    intent: str | None  # match_intent() / DEC-ROUTE label; None = unknown
    est_input_tokens: int
    needs_tools: bool
    needs_structured_output: bool
    risk_tier: RiskTier = RiskTier.LOW
    language: str = "en"  # BCP-47 primary subtag
    tainted: bool = False  # Phase H taint flag for the current turn


@dataclass(frozen=True)
class ModelChoice:
    """A policy's answer: which model ref, at which tier, and why."""

    model_ref: str  # LiteLLM string or "gw:<gateway>/<model>"
    tier: ModelTier
    # static | budget_soft_cap | cascade_start | escalation:<cause> | batch | fallback:<err>
    reason: str
    escalation_of: ModelChoice | None = None
    policy: str = "static"

    @property
    def escalation_count(self) -> int:
        """Number of escalations behind this choice."""
        n, cur = 0, self.escalation_of
        while cur is not None:
            n += 1
            cur = cur.escalation_of
        return n


@dataclass(frozen=True)
class RoutingOutcome:
    """What happened when the chosen model ran."""

    success: bool
    structured_valid: bool | None = None
    adequacy: float | None = None  # DecisionPort Noul P(true), when computed
    tool_errors: int = 0
    # True when the adequacy check ran: adequacy=None then means it abstained
    # (escalate for high/critical risk), not that no checker exists.
    adequacy_checked: bool = False
    # Features of the call this outcome belongs to (filled by record_outcome) so a
    # policy never reads another agent's cached features (risk, capabilities).
    features: TaskFeatures | None = None
    usage: Usage | None = None  # TKE1 Usage incl. cache fields
    served_model: str | None = None  # gateway-reported model, may differ from model_ref
