"""Shared helpers for decorator policies (Phase AMR — DD-AMR-001, SEC-001)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from prismal.routing.policies.static import StaticTierPolicy
from prismal.routing.types import ModelChoice, ModelTier, max_tier

if TYPE_CHECKING:
    from collections.abc import Mapping

    from prismal.decisions.types import RiskTier
    from prismal.routing.ports import RoutingPolicyPort
    from prismal.routing.types import TaskFeatures


def static_of(policy: RoutingPolicyPort) -> StaticTierPolicy | None:
    """The :class:`StaticTierPolicy` at the core of a decorator chain, if any."""
    seen: set[int] = set()
    cur: object = policy
    while cur is not None and id(cur) not in seen:
        if isinstance(cur, StaticTierPolicy):
            return cur
        seen.add(id(cur))
        cur = getattr(cur, "inner", None)
    return None


def floor_for(features: TaskFeatures, floor: Mapping[RiskTier, ModelTier]) -> ModelTier | None:
    """The minimum tier for *features*' risk, or None when unconstrained."""
    return floor.get(features.risk_tier)


def clamp_to_floor(
    choice: ModelChoice,
    features: TaskFeatures,
    floor: Mapping[RiskTier, ModelTier],
    static: StaticTierPolicy,
) -> ModelChoice:
    """Raise *choice* to the risk floor (fail-closed); ``AUTO`` banned for critical."""
    minimum = floor_for(features, floor)
    if choice.tier is ModelTier.AUTO:
        if features.risk_tier.value != "critical":
            return choice
        target = minimum or ModelTier.FRONTIER
    else:
        if minimum is None or max_tier(choice.tier, minimum) is choice.tier:
            return choice
        target = minimum
    return ModelChoice(
        model_ref=static.model_for(target),
        tier=target,
        reason=f"{choice.reason}+floor",
        escalation_of=choice.escalation_of,
        policy=choice.policy,
    )
