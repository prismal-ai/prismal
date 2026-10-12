"""``BudgetAwarePolicy`` — one-tier downgrade on a soft cap (Phase AMR — SPEC-AMR-POL-002).

Hard caps are untouched: ``BudgetGuard.enforce()`` aborts the run and AMR never
masks that. Downgrades never go below the risk floor and never apply to a
tainted turn (SEC-001). ``AUTO`` is terminal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prismal.core.config import get_settings
from prismal.routing.policies._base import floor_for, static_of
from prismal.routing.policies.static import StaticTierPolicy, resolve_risk_floor
from prismal.routing.types import ModelChoice, ModelTier, tier_at_least, tier_shift

if TYPE_CHECKING:
    from collections.abc import Mapping

    from prismal.budget.types import BudgetStatus
    from prismal.core.config import Settings
    from prismal.decisions.types import RiskTier
    from prismal.routing.ports import RoutingPolicyPort
    from prismal.routing.types import RoutingOutcome, TaskFeatures


class BudgetAwarePolicy:
    """Decorator: downgrade the inner choice by one tier while over the soft cap."""

    name = "budget_aware"

    def __init__(
        self,
        inner: RoutingPolicyPort,
        *,
        settings: Settings | None = None,
        floor: Mapping[RiskTier, ModelTier] | None = None,
    ) -> None:
        self.inner = inner
        self._settings = settings or get_settings()
        self._static = static_of(inner) or StaticTierPolicy(settings=self._settings)
        self._floor = (
            dict(floor)
            if floor is not None
            else (self._static.floor or resolve_risk_floor(self._settings))
        )

    def select(self, features: TaskFeatures, *, budget: BudgetStatus | None = None) -> ModelChoice:
        """The inner choice, downgraded one tier while over the soft cap.

        No downgrade on a hard cap, a tainted turn, ``AUTO`` or below the risk floor.
        """
        choice = self.inner.select(features, budget=budget)
        if budget is None or budget.hard_exceeded or not budget.soft_exceeded:
            return choice
        if features.tainted or choice.tier is ModelTier.AUTO:
            return choice
        target = tier_shift(choice.tier, -1)
        minimum = floor_for(features, self._floor)
        if target is choice.tier or (minimum is not None and not tier_at_least(target, minimum)):
            return choice
        return ModelChoice(
            model_ref=self._static.model_for(target),
            tier=target,
            reason="budget_soft_cap",
            escalation_of=choice.escalation_of,
            policy=self.name,
        )

    def on_outcome(self, choice: ModelChoice, outcome: RoutingOutcome) -> ModelChoice | None:
        """Delegate escalation to the inner policy."""
        return self.inner.on_outcome(choice, outcome)
