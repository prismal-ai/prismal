"""Per-run routing: seeding, ``select_model`` and ``record_outcome`` (Phase AMR — SPEC-AMR-INT-001).

Mirrors ``budget/resolve.py``: live objects (policy, predictor, per-agent
choices) live in an **in-process registry keyed by session_id**; checkpointed
state only carries the serializable view under ``state["metadata"]["routing"]``
(``enabled``, ``policy``, ``last``, ``affinity``, ``escalations``, ``shadow``).

Order of a selection (ADR-WAVE-001 step 1):

1. same-turn sticky choice / cross-turn escalation affinity (CAS-002, DD-AMR-003);
2. ``policy.select`` — fail-open: any exception ⇒ static choice ``fallback:<err>``;
3. security floors — fail-closed: risk floor, no ``AUTO`` for critical, tainted
   turns never below the agent's static tier (SEC-001);
4. ``shadow`` mode records the choice but returns the 3.15.0 model.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from prismal.core.config import get_settings
from prismal.core.logging import get_logger
from prismal.monitoring.otel import OTelManager
from prismal.providers.cost import compute_cost_usd
from prismal.routing.features import extract_features
from prismal.routing.policies._base import clamp_to_floor, static_of
from prismal.routing.policies.budget_aware import BudgetAwarePolicy
from prismal.routing.policies.cascade import CascadePolicy
from prismal.routing.policies.composite import CompositePolicy, fallback_choice
from prismal.routing.policies.static import StaticTierPolicy
from prismal.routing.types import ModelChoice, ModelTier, max_tier

if TYPE_CHECKING:
    from collections.abc import Mapping

    from prismal.budget.types import BudgetStatus
    from prismal.core.config import Settings
    from prismal.routing.policies.cascade import AdequacyChecker
    from prismal.routing.ports import RoutingPolicyPort, SuccessPredictorPort
    from prismal.routing.types import RoutingOutcome, TaskFeatures

logger = get_logger(__name__)

# session_id -> {"policy", "static", "predictor", "turn", "turn_choices", "affinity", "features"}
# LRU-bounded: hosts that never call clear_routing_run cannot grow it without limit.
_RUNS: OrderedDict[str, dict[str, Any]] = OrderedDict()
_MAX_RUNS = 1024
_DEFAULT_KEY = "_default"


def _store(key: str, bucket: dict[str, Any]) -> None:
    _RUNS[key] = bucket
    _RUNS.move_to_end(key)
    while len(_RUNS) > _MAX_RUNS:
        _RUNS.popitem(last=False)


def _bucket(state: Any) -> dict[str, Any] | None:
    key = _run_key(state)
    bucket = _RUNS.get(key)
    if bucket is not None:
        _RUNS.move_to_end(key)
    return bucket


def _run_key(state: Mapping[str, Any]) -> str:
    sid = state.get("session_id") if isinstance(state, dict) else None
    return str(sid) if sid else _DEFAULT_KEY


def _turn_signature(state: Mapping[str, Any]) -> tuple[int, str]:
    """(human-message count, hash of the last human message).

    The hash keeps the signature changing across turns even when context
    compaction keeps the human-message count constant.
    """
    messages = state.get("messages") if isinstance(state, dict) else None
    if not isinstance(messages, list):
        return (0, "")
    humans = [m for m in messages if getattr(m, "type", None) == "human"]
    if not humans:
        return (0, "")
    last = humans[-1]
    ident = getattr(last, "id", None) or str(getattr(last, "content", ""))
    return (len(humans), hashlib.sha256(str(ident).encode("utf-8")).hexdigest()[:16])


def _routing_meta(state: Any) -> dict[str, Any]:
    if not isinstance(state, dict):
        return {}
    metadata = state.setdefault("metadata", {})
    routing = metadata.get("routing")
    if not isinstance(routing, dict):
        routing = {}
        metadata["routing"] = routing
    return routing


def _view(choice: ModelChoice) -> dict[str, Any]:
    return {"tier": choice.tier.value, "model_ref": choice.model_ref}


def _affinity_view(choice: ModelChoice) -> dict[str, Any]:
    return {**_view(choice), "escalations": choice.escalation_count}


def _restore_affinity(view: Any, static: StaticTierPolicy, policy_name: str) -> ModelChoice | None:
    """Rebuild a persisted affinity, only if it matches the tier map (finding: forged state)."""
    if not isinstance(view, dict):
        return None
    try:
        tier = ModelTier(view["tier"])
        ref = str(view["model_ref"])
        count = max(0, int(view.get("escalations", 1)))
    except (KeyError, TypeError, ValueError):
        return None
    if ref != static.model_for(tier):
        logger.warning("routing.affinity_rejected", tier=tier.value)
        return None
    prev: ModelChoice | None = None
    for _ in range(count):  # rebuild the chain so the escalation cap survives restarts
        prev = ModelChoice(model_ref=ref, tier=tier, reason="affinity", escalation_of=prev)
    return ModelChoice(
        model_ref=ref, tier=tier, reason="affinity", escalation_of=prev, policy=policy_name
    )


def build_policy(
    settings: Settings, *, adequacy: AdequacyChecker | None = None
) -> RoutingPolicyPort:
    """The top-level policy for ``settings.routing_policy`` (DD-AMR-001).

    ``batch`` selects single calls statically; fan-outs use
    ``BatchAssignmentPolicy.assign()`` directly (SPEC-AMR-INT-002).
    """
    static = StaticTierPolicy(settings=settings)
    match settings.routing_policy:
        case "cascade":
            return CascadePolicy(static, settings=settings, adequacy=adequacy)
        case "budget_aware":
            return BudgetAwarePolicy(static, settings=settings)
        case "composite":
            cascade = CascadePolicy(static, settings=settings, adequacy=adequacy)
            budget = BudgetAwarePolicy(cascade, settings=settings)
            return CompositePolicy([static, cascade, budget], settings=settings)
        case _:
            return static


def seed_routing_run(
    state: Any,
    settings: Settings,
    *,
    session_id: str | None = None,
    policy: RoutingPolicyPort | None = None,
    predictor: SuccessPredictorPort | None = None,
    adequacy: AdequacyChecker | None = None,
) -> None:
    """Install the per-run routing engine; no-op unless ``adaptive_routing_enabled``."""
    if not settings.adaptive_routing_enabled:
        return
    key = session_id or _run_key(state)
    if key == _DEFAULT_KEY:
        return  # no session ⇒ stateless selection (never a shared process-wide bucket)
    previous = _RUNS.get(key)
    policy = policy or build_policy(settings, adequacy=adequacy)
    static = static_of(policy) or StaticTierPolicy(settings=settings)
    affinity: dict[str, ModelChoice] = dict(previous["affinity"]) if previous else {}
    routing = _routing_meta(state)
    for agent, view in (routing.get("affinity") or {}).items():
        if agent not in affinity:
            restored = _restore_affinity(view, static, policy.name)
            if restored is not None:
                affinity[agent] = restored
    if adequacy is None and previous is None:
        adequacy = _default_adequacy(settings)
    _store(
        key,
        {
            "policy": policy,
            "static": static,
            "predictor": predictor if predictor is not None else (previous or {}).get("predictor"),
            "turn": _turn_signature(state),
            "turn_choices": {},
            "affinity": affinity,
            "features": {},
            "settings": settings,
            "adequacy": adequacy if adequacy is not None else (previous or {}).get("adequacy"),
        },
    )
    routing["enabled"] = True
    routing["policy"] = policy.name
    routing["mode"] = settings.routing_mode


def maybe_seed_routing_run(state: Any, settings: Settings) -> None:
    """Seed once per turn (idempotent across hops); affinity carries across turns."""
    if not settings.adaptive_routing_enabled:
        return
    if _run_key(state) == _DEFAULT_KEY:
        return
    existing = _bucket(state)
    if existing is not None and existing.get("turn") == _turn_signature(state):
        return
    seed_routing_run(
        state,
        settings,
        policy=existing["policy"] if existing else None,
        predictor=existing["predictor"] if existing else None,
        adequacy=existing.get("adequacy") if existing else None,
    )


def _default_adequacy(settings: Settings) -> AdequacyChecker | None:
    """A DEC-backed adequacy checker when typed decisions are enabled, else None."""
    if not settings.decisions_enabled:
        return None
    try:
        from prismal.decisions.runtime import get_decision_runtime
        from prismal.routing.policies.cascade import AdequacyChecker

        runtime = get_decision_runtime(settings)
        if runtime is None:
            return None
        return AdequacyChecker.from_cascade(
            runtime.cascade,
            tau=settings.routing_adequacy_tau,
            timeout_ms=settings.routing_adequacy_timeout_ms,
        )
    except Exception as exc:
        logger.warning("routing.adequacy_unavailable", error=type(exc).__name__)
        return None


def get_adequacy_checker(state: Any) -> AdequacyChecker | None:
    """The per-run adequacy checker, or None."""
    bucket = _bucket(state)
    return bucket.get("adequacy") if bucket else None


def get_routing_features(state: Any, agent: str) -> TaskFeatures | None:
    """Features of *agent*'s current selection in this run, or None."""
    bucket = _bucket(state)
    return bucket["features"].get(agent) if bucket else None


def get_routing_policy(state: Any) -> RoutingPolicyPort | None:
    """The per-run policy, or None when disabled/unseeded."""
    bucket = _bucket(state)
    return bucket["policy"] if bucket else None


def clear_routing_run(state: Any) -> None:
    """Release the per-run engine (idempotent)."""
    _RUNS.pop(_run_key(state), None)


def _budget_status(state: Any) -> BudgetStatus | None:
    try:
        from prismal.budget.resolve import get_budget_guard

        guard = get_budget_guard(state)
        return guard.check() if guard is not None else None
    except Exception:
        return None


def _apply_security(
    choice: ModelChoice, features: TaskFeatures, static: StaticTierPolicy
) -> ModelChoice:
    """Fail-closed floors (SEC-001)."""
    if features.tainted:
        base = static.select(features)
        if base.tier is ModelTier.AUTO:
            below_static = False
        elif choice.tier is ModelTier.AUTO:
            below_static = True  # an unpredictable auto router never serves a tainted turn
        else:
            below_static = max_tier(choice.tier, base.tier) is not choice.tier
        if below_static:
            choice = ModelChoice(
                model_ref=base.model_ref,
                tier=base.tier,
                reason=f"{choice.reason}+tainted",
                escalation_of=choice.escalation_of,
                policy=choice.policy,
            )
    return clamp_to_floor(choice, features, static.floor, static)


def _with_floor_on_affinity(affinity: ModelChoice, fresh: ModelChoice) -> ModelChoice:
    """No re-descent: keep the escalated affinity unless the fresh choice is higher."""
    if ModelTier.AUTO in (affinity.tier, fresh.tier):
        return affinity
    return affinity if max_tier(affinity.tier, fresh.tier) is affinity.tier else fresh


def select_model(
    state: Any,
    agent: str,
    *,
    settings: Settings | None = None,
    features: TaskFeatures | None = None,
) -> ModelChoice:
    """Choose the model for *agent* in this run (never raises)."""
    settings = settings or get_settings()
    if not settings.adaptive_routing_enabled:
        return ModelChoice(model_ref=settings.default_model, tier=ModelTier.MEDIUM, reason="static")
    maybe_seed_routing_run(state, settings)
    bucket: dict[str, Any] | None = _bucket(state)
    if bucket is None:
        # No session id: stateless selection, nothing stored or shared across callers.
        bucket = {
            "policy": build_policy(settings),
            "turn_choices": {},
            "affinity": {},
            "features": {},
        }
        bucket["static"] = static_of(bucket["policy"]) or StaticTierPolicy(settings=settings)
    policy: RoutingPolicyPort = bucket["policy"]
    static: StaticTierPolicy = bucket["static"]
    otel = OTelManager()
    features = features or extract_features(state, agent, settings=settings)

    sticky: ModelChoice | None = bucket["turn_choices"].get(agent)
    choice: ModelChoice
    if sticky is not None and settings.routing_cache_affinity_enabled:
        choice = sticky
    else:
        try:
            choice = policy.select(features, budget=_budget_status(state))
        except Exception as exc:
            logger.warning("routing.select_failed", policy=policy.name, error=type(exc).__name__)
            choice = fallback_choice(static, features, exc)
        affinity = bucket["affinity"].get(agent)
        if affinity is not None and settings.routing_cache_affinity_enabled:
            choice = _with_floor_on_affinity(affinity, choice)
    # Floors run on every hop, sticky ones included: taint can arrive mid-turn.
    choice = _apply_security(choice, features, static)

    bucket["turn_choices"][agent] = choice
    bucket["features"][agent] = features
    routing = _routing_meta(state)
    with otel.start_span(
        "prismal.routing.select",
        attributes={
            "policy": choice.policy,
            "tier": choice.tier.value,
            "model_ref": choice.model_ref,
            "reason": choice.reason,
        },
    ):
        otel.increment_counter(
            "routing_decisions",
            attributes={"policy": choice.policy, "tier": choice.tier.value, **_tenant_attr(state)},
        )
    if settings.routing_mode == "shadow":
        routing.setdefault("shadow", {})[agent] = {**_view(choice), "reason": choice.reason}
        return ModelChoice(
            model_ref=settings.default_model,
            tier=static.tier_for_agent(agent),
            reason="shadow",
            policy=choice.policy,
        )
    routing.setdefault("last", {})[agent] = {**_view(choice), "reason": choice.reason}
    return choice


def _tenant_attr(state: Any) -> dict[str, str]:
    """``{"tenant": org_id}`` when the run's metadata names one, else ``{}`` (HRS)."""
    metadata = state.get("metadata") if isinstance(state, dict) else None
    org_id = metadata.get("org_id") if isinstance(metadata, dict) else None
    return {"tenant": str(org_id)} if org_id else {}


def _record_savings(
    choice: ModelChoice,
    outcome: RoutingOutcome,
    static: StaticTierPolicy,
    settings: Settings,
    *,
    tenant: Mapping[str, str] | None = None,
) -> None:
    usage = outcome.usage
    if usage is None:
        return
    try:
        frontier = static.model_for(ModelTier.FRONTIER)
        counterfactual = compute_cost_usd(
            frontier, usage.prompt_tokens, usage.completion_tokens, settings=settings
        ).cost_usd
        saved_micro = round((counterfactual - usage.cost_usd) * 1_000_000)
    except Exception:
        return
    if saved_micro > 0:
        OTelManager().increment_counter(
            "routing_savings_usd",
            saved_micro,
            attributes={"policy": choice.policy, "tier": choice.tier.value, **(tenant or {})},
        )


def record_outcome(state: Any, agent: str, outcome: RoutingOutcome) -> ModelChoice | None:
    """Feed an outcome back; returns the escalated choice to retry with, or None."""
    bucket = _bucket(state)
    if bucket is None:
        return None
    choice: ModelChoice | None = bucket["turn_choices"].get(agent)
    if choice is None:
        return None
    settings: Settings = bucket["settings"]
    policy: RoutingPolicyPort = bucket["policy"]
    static: StaticTierPolicy = bucket["static"]
    if outcome.features is None and agent in bucket["features"]:
        outcome = replace(outcome, features=bucket["features"][agent])
    usage = outcome.usage
    if usage is not None and usage.cost_usd == 0.0 and usage.total_tokens > 0:
        try:  # price the call at the chosen model so savings are real, not inflated
            cost = compute_cost_usd(
                outcome.served_model or choice.model_ref,
                usage.prompt_tokens,
                usage.completion_tokens,
                settings=settings,
            ).cost_usd
            outcome = replace(outcome, usage=replace(usage, cost_usd=cost))
        except Exception as exc:
            logger.debug("routing.usage_pricing_failed", error=type(exc).__name__)
    try:
        escalated = policy.on_outcome(choice, outcome)
    except Exception as exc:
        logger.warning("routing.on_outcome_failed", error=type(exc).__name__)
        escalated = None
    routing = _routing_meta(state)
    if escalated is None:
        predictor = bucket.get("predictor")
        features = bucket["features"].get(agent)
        if predictor is not None and features is not None:
            try:
                predictor.update(
                    features, outcome.served_model or choice.model_ref, outcome.success
                )
            except Exception as exc:
                logger.warning("routing.predictor_update_failed", error=type(exc).__name__)
        if settings.routing_mode == "enforce":  # shadow choices never ran
            _record_savings(choice, outcome, static, settings, tenant=_tenant_attr(state))
        return None
    features = bucket["features"].get(agent)
    if features is not None:
        escalated = _apply_security(escalated, features, static)
    if settings.routing_mode == "shadow":
        counts = routing.setdefault("shadow_escalations", {})
        counts[agent] = int(counts.get(agent, 0)) + 1
        return None
    bucket["turn_choices"][agent] = escalated
    if settings.routing_cache_affinity_enabled:
        bucket["affinity"][agent] = escalated
        routing.setdefault("affinity", {})[agent] = _affinity_view(escalated)
    counts = routing.setdefault("escalations", {})
    counts[agent] = int(counts.get(agent, 0)) + 1
    routing.setdefault("last", {})[agent] = {**_view(escalated), "reason": escalated.reason}
    return escalated
