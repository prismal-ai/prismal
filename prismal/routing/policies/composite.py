"""``CompositePolicy`` — an ordered chain of policies (Phase AMR — SPEC-AMR-POL-003).

``chain`` is listed **innermost first**. ``select`` asks the outermost policy and
falls back inward when one raises; when every policy raises, the static choice
is returned with ``reason="fallback:<error>"`` (fail-open on cost). ``on_outcome``
is offered innermost-first and the first non-``None`` escalation wins.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prismal.core.config import get_settings
from prismal.core.logging import get_logger
from prismal.monitoring.otel import OTelManager
from prismal.routing.policies._base import static_of
from prismal.routing.policies.static import StaticTierPolicy
from prismal.routing.types import ModelChoice

if TYPE_CHECKING:
    from collections.abc import Sequence

    from prismal.budget.types import BudgetStatus
    from prismal.core.config import Settings
    from prismal.routing.ports import RoutingPolicyPort
    from prismal.routing.types import RoutingOutcome, TaskFeatures

logger = get_logger(__name__)


def fallback_choice(
    static: StaticTierPolicy, features: TaskFeatures, exc: BaseException
) -> ModelChoice:
    """The static choice tagged ``fallback:<error class>`` (SPEC-AMR-TYP-002)."""
    base = static.select(features)
    OTelManager().increment_counter(
        "routing_decisions", attributes={"policy": "fallback", "tier": base.tier.value}
    )
    return ModelChoice(
        model_ref=base.model_ref,
        tier=base.tier,
        reason=f"fallback:{type(exc).__name__}",
        policy="fallback",
    )


class CompositePolicy:
    """Chain of policies with inward fallback and first-escalation-wins."""

    name = "composite"

    def __init__(
        self, chain: Sequence[RoutingPolicyPort], *, settings: Settings | None = None
    ) -> None:
        if not chain:
            raise ValueError("CompositePolicy needs at least one policy")
        self.chain = tuple(chain)
        self.inner = self.chain[-1]
        self._settings = settings
        found = next((s for s in (static_of(p) for p in self.chain) if s is not None), None)
        self._static = found

    def _fallback_static(self) -> StaticTierPolicy:
        if self._static is None:
            self._static = StaticTierPolicy(settings=self._settings or get_settings())
        return self._static

    def select(self, features: TaskFeatures, *, budget: BudgetStatus | None = None) -> ModelChoice:
        """First successful choice, innermost policy outward; static fallback if all fail."""
        last: BaseException | None = None
        for policy in reversed(self.chain):
            try:
                return policy.select(features, budget=budget)
            except Exception as exc:
                logger.warning(
                    "routing.policy_failed", policy=policy.name, error=type(exc).__name__
                )
                last = exc
        assert last is not None
        return fallback_choice(self._fallback_static(), features, last)

    def on_outcome(self, choice: ModelChoice, outcome: RoutingOutcome) -> ModelChoice | None:
        """First escalation proposed along the chain (failing policies skipped)."""
        for policy in self.chain:
            try:
                escalated = policy.on_outcome(choice, outcome)
            except Exception as exc:
                logger.warning(
                    "routing.on_outcome_failed", policy=policy.name, error=type(exc).__name__
                )
                continue
            if escalated is not None:
                return escalated
        return None
