"""Routing value objects, ports, exceptions and metrics (SPEC-AMR-TYP-001/002, ERR-001, OTEL-001)."""

from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock

import pytest

from prismal.core.exceptions import (
    BatchAssignmentError,
    BatchExecutorError,
    PlanCacheError,
    PrismalError,
    RoutingConfigError,
    RoutingError,
)
from prismal.decisions.types import RiskTier
from prismal.monitoring.otel import OTelManager
from prismal.routing.ports import RoutingPolicyPort, SuccessPredictorPort
from prismal.routing.types import (
    TIER_ORDER,
    ModelChoice,
    ModelTier,
    RoutingOutcome,
    TaskFeatures,
    tier_at_least,
    tier_index,
    tier_shift,
)


def _features(**kw: object) -> TaskFeatures:
    base: dict[str, object] = {
        "agent": "coder",
        "intent": None,
        "est_input_tokens": 100,
        "needs_tools": False,
        "needs_structured_output": False,
    }
    base.update(kw)
    return TaskFeatures(**base)  # type: ignore[arg-type]


def test_tier_order_excludes_auto() -> None:
    assert TIER_ORDER == (ModelTier.NANO, ModelTier.SMALL, ModelTier.MEDIUM, ModelTier.FRONTIER)
    assert ModelTier.AUTO not in TIER_ORDER


def test_tier_index_and_comparison_through_order() -> None:
    assert tier_index(ModelTier.NANO) == 0
    assert tier_index(ModelTier.FRONTIER) == 3
    assert tier_at_least(ModelTier.MEDIUM, ModelTier.SMALL)
    assert not tier_at_least(ModelTier.SMALL, ModelTier.MEDIUM)


def test_auto_is_terminal_for_shifts() -> None:
    assert tier_shift(ModelTier.AUTO, +1) is ModelTier.AUTO
    assert tier_shift(ModelTier.AUTO, -1) is ModelTier.AUTO


def test_tier_shift_clamps_at_ends() -> None:
    assert tier_shift(ModelTier.SMALL, +1) is ModelTier.MEDIUM
    assert tier_shift(ModelTier.FRONTIER, +1) is ModelTier.FRONTIER
    assert tier_shift(ModelTier.NANO, -1) is ModelTier.NANO


def test_auto_has_no_index() -> None:
    with pytest.raises(ValueError, match="auto"):
        tier_index(ModelTier.AUTO)


def test_task_features_defaults_and_frozen() -> None:
    f = _features()
    assert f.risk_tier is RiskTier.LOW
    assert f.language == "en"
    assert f.tainted is False
    with pytest.raises(dataclasses.FrozenInstanceError):
        f.agent = "x"  # type: ignore[misc]


def test_model_choice_chain() -> None:
    first = ModelChoice(model_ref="small-m", tier=ModelTier.SMALL, reason="cascade_start")
    second = ModelChoice(
        model_ref="medium-m",
        tier=ModelTier.MEDIUM,
        reason="escalation:structured_invalid",
        escalation_of=first,
        policy="cascade",
    )
    assert second.escalation_of is first
    assert first.policy == "static"
    assert second.escalation_count == 1
    assert first.escalation_count == 0


def test_routing_outcome_defaults() -> None:
    o = RoutingOutcome(success=True)
    assert o.structured_valid is None
    assert o.adequacy is None
    assert o.tool_errors == 0
    assert o.usage is None
    assert o.served_model is None


class _Policy:
    name = "p"

    def select(self, features: TaskFeatures, *, budget: object = None) -> ModelChoice:
        return ModelChoice(model_ref="m", tier=ModelTier.SMALL, reason="static")

    def on_outcome(self, choice: ModelChoice, outcome: RoutingOutcome) -> ModelChoice | None:
        return None


class _Predictor:
    version = "v"

    def p_success(self, features: TaskFeatures, model_ref: str) -> float:
        return 0.5

    def expected_output_tokens(self, features: TaskFeatures, model_ref: str) -> int:
        return 10

    def update(self, features: TaskFeatures, model_ref: str, success: bool) -> None:
        return None


def test_ports_are_runtime_checkable() -> None:
    assert isinstance(_Policy(), RoutingPolicyPort)
    assert isinstance(_Predictor(), SuccessPredictorPort)
    assert not isinstance(object(), RoutingPolicyPort)


def test_routing_exception_tree() -> None:
    for exc in (RoutingConfigError, BatchAssignmentError, PlanCacheError, BatchExecutorError):
        assert issubclass(exc, RoutingError)
    assert issubclass(RoutingError, PrismalError)


def test_routing_metrics_registered() -> None:
    mgr = object.__new__(OTelManager)
    mgr._counters = {}
    mgr._histograms = {}
    mgr._meter = MagicMock()
    mgr._register_standard_metrics()
    created = {c.args[0] for c in mgr._meter.create_counter.call_args_list}
    expected = {
        "routing_decisions": "prismal.routing_decisions_total",
        "routing_escalations": "prismal.routing_escalations_total",
        "plan_cache_hits": "prismal.plan_cache_hits_total",
        "plan_cache_misses": "prismal.plan_cache_misses_total",
        "batch_jobs": "prismal.batch_jobs_total",
        "routing_savings_usd": "prismal.routing_savings_usd",
    }
    for key, name in expected.items():
        assert key in mgr._counters, key
        assert name in created, name
