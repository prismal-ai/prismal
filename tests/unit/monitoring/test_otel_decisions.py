"""Decision metrics registered in OTelManager (Phase DEC — SPEC-DEC-OTEL-001)."""

from __future__ import annotations

from unittest.mock import MagicMock

from prismal.core.exceptions import (
    DecisionBackendError,
    DecisionCalibrationError,
    DecisionConfigError,
    DecisionError,
    ForbiddenDecisionError,
    PrismalError,
)
from prismal.monitoring.otel import OTelManager

_DEC_COUNTERS = {
    "decisions": "prismal.decisions_total",
    "llm_calls_avoided": "prismal.llm_calls_avoided_total",
    "decision_drift_alarms": "prismal.decision_drift_alarms_total",
    "decision_injection_flags": "prismal.decision_injection_flags_total",
    "decision_shuffle_inconsistent": "prismal.decision_shuffle_inconsistent_total",
    "decision_model_mismatch": "prismal.decision_model_mismatch_total",
}


def _registered() -> OTelManager:
    mgr = object.__new__(OTelManager)
    mgr._counters = {}
    mgr._histograms = {}
    mgr._meter = MagicMock()
    mgr._register_standard_metrics()
    return mgr


def test_decision_counters_registered_with_spec_names() -> None:
    mgr = _registered()
    created = {c.args[0] for c in mgr._meter.create_counter.call_args_list}
    for key, name in _DEC_COUNTERS.items():
        assert key in mgr._counters
        assert name in created


def test_decision_latency_histogram_and_agreement_gauge() -> None:
    mgr = _registered()
    hist = {c.args[0] for c in mgr._meter.create_histogram.call_args_list}
    gauges = {c.args[0] for c in mgr._meter.create_gauge.call_args_list}
    assert "prismal.decision_latency_ms" in hist
    assert "decision_latency_ms" in mgr._histograms
    assert "prismal.decision_shadow_agreement_ratio" in gauges
    assert "decision_shadow_agreement_ratio" in mgr._gauges


def test_decision_exception_tree() -> None:
    for exc in (
        DecisionConfigError,
        DecisionCalibrationError,
        ForbiddenDecisionError,
        DecisionBackendError,
    ):
        assert issubclass(exc, DecisionError)
    assert issubclass(DecisionError, PrismalError)
