"""Fan-out model assignment for Skynet / MoA (Phase AMR — SPEC-AMR-INT-002).

``assign_fanout`` hands one round of independent items to a batch policy
(``BatchAssignmentPolicy.assign``) and returns ``item_id -> ModelChoice``. It is
fail-open and never raises: routing disabled, no policy or any solver error
yields ``None`` and the caller keeps its static models.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from prismal.core.logging import get_logger
from prismal.routing.types import TaskFeatures

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from prismal.core.config import Settings
    from prismal.routing.types import ModelChoice

logger = get_logger(__name__)


class BatchAssignerPort(Protocol):
    """The slice of ``BatchAssignmentPolicy`` the fan-out integrations need."""

    def assign(self, items: Sequence[Any]) -> Any:
        """Return an object with ``choices: Mapping[item_id, ModelChoice]``."""
        ...


def fanout_features(
    agent: str, text: str, *, needs_tools: bool = True, tainted: bool = False
) -> TaskFeatures:
    """Deterministic features for one fan-out item.

    The item text is user-derived: it is tainted when the caller says so or the
    active run's taint registry marks it untrusted (SEC-001 applies to fan-outs).
    """
    from prismal.security.taint import get_active_taint_registry
    from prismal.utils.token_estimate import estimate_tokens

    registry = get_active_taint_registry()
    return TaskFeatures(
        agent=agent,
        intent=None,
        est_input_tokens=estimate_tokens(text),
        needs_tools=needs_tools,
        needs_structured_output=False,
        tainted=tainted or (registry is not None and registry.is_untrusted(text)),
    )


def assign_fanout(
    items: Sequence[tuple[str, TaskFeatures]],
    policy: BatchAssignerPort | None,
    settings: Settings,
) -> Mapping[str, ModelChoice] | None:
    """``item_id -> ModelChoice`` for one round, or None (disabled / failure)."""
    if policy is None or not settings.adaptive_routing_enabled or not items:
        return None
    from prismal.routing.policies.batch_assignment import BatchItem

    try:
        assignment = policy.assign([BatchItem(item_id=i, features=f) for i, f in items])
        choices: Mapping[str, ModelChoice] = assignment.choices
    except Exception as exc:
        logger.warning("routing.fanout_failed", error=type(exc).__name__)
        return None
    return choices


def build_batch_policy(settings: Settings) -> BatchAssignerPort | None:
    """A ``BatchAssignmentPolicy`` from settings when ``routing_policy="batch"``, else None.

    Candidates are the configured ordered-tier models of the tier map; the
    predictor comes from ``routing_predictor_path``. Any configuration error is
    logged and yields None (the fan-out keeps its static models).
    """
    if not settings.adaptive_routing_enabled or settings.routing_policy != "batch":
        return None
    from prismal.routing.policies.batch_assignment import BatchAssignmentPolicy
    from prismal.routing.policies.static import StaticTierPolicy
    from prismal.routing.predictor import load_predictor_table
    from prismal.routing.types import TIER_ORDER, ModelTier, tier_at_least

    try:
        static = StaticTierPolicy(settings=settings)
        tier_by_model = {static.model_for(t): t for t in TIER_ORDER if static.has_tier(t)}

        def tier_of(ref: str) -> ModelTier:
            return tier_by_model[ref]

        def eligible(features: TaskFeatures, ref: str) -> bool:
            tier = tier_by_model[ref]
            if features.tainted:  # SEC-001: tainted never below the agent's static tier
                static_tier = static.tier_for_agent(features.agent)
                if static_tier is not ModelTier.AUTO and not tier_at_least(tier, static_tier):
                    return False
            return _gateway_allows(settings, ref, features.agent)

        def price(ref: str, tokens_in: int, tokens_out: int) -> float:
            from prismal.providers.cost import compute_cost_usd

            return compute_cost_usd(ref, tokens_in, tokens_out, settings=settings).cost_usd

        predictor = load_predictor_table(str(settings.routing_predictor_path), tier_of=tier_of)
        return BatchAssignmentPolicy(
            predictor=predictor,
            candidates=list(tier_by_model),
            settings=settings,
            price_fn=price,
            tier_of=tier_of,
            floor={risk.value: tier.value for risk, tier in static.floor.items()},
            eligible_fn=eligible,
        )
    except Exception as exc:
        logger.warning("routing.batch_policy_unavailable", error=type(exc).__name__)
        return None


def _gateway_allows(settings: Settings, ref: str, agent: str) -> bool:
    """SPEC-AMR-GW-001: a gateway policy denial makes the candidate ineligible.

    ``GatewayRouter.resolve`` is pure (no I/O); a policy error ⇒ ineligible
    (fail closed). Non-gateway refs and gateways-off are always allowed.
    """
    if not settings.llm_gateways_enabled:
        return True
    from prismal.core.exceptions import GatewayError
    from prismal.providers.gateways.router import get_gateway_router

    router = get_gateway_router()
    if router is None:
        return not ref.startswith("gw:")
    try:
        router.resolve(ref, agent=agent, session_id=None)
    except GatewayError:
        return False
    return True
