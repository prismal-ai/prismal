"""Gateway metrics registered in OTelManager (Phase GW — SPEC-GW-OTEL-001)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from prismal.monitoring.otel import OTelManager

_GW_COUNTERS = {
    "gateway_requests": "prismal.gateway_requests_total",
    "gateway_fallbacks": "prismal.gateway_fallbacks_total",
    "gateway_policy_denials": "prismal.gateway_policy_denials_total",
    "gateway_errors": "prismal.gateway_errors_total",
}


def _registered() -> OTelManager:
    mgr = object.__new__(OTelManager)
    mgr._counters = {}
    mgr._histograms = {}
    mgr._meter = MagicMock()
    mgr._register_standard_metrics()
    return mgr


def test_gateway_counters_registered_with_spec_names() -> None:
    mgr = _registered()
    created = {c.args[0] for c in mgr._meter.create_counter.call_args_list}
    for key, name in _GW_COUNTERS.items():
        assert key in mgr._counters
        assert name in created


def test_gateway_drift_histogram_registered() -> None:
    mgr = _registered()
    created = {c.args[0] for c in mgr._meter.create_histogram.call_args_list}
    assert "gateway_cost_drift_usd" in mgr._histograms
    assert "prismal.gateway_cost_drift_usd" in created


def test_gateway_quota_gauge_registered_and_set() -> None:
    mgr = _registered()
    created = {c.args[0] for c in mgr._meter.create_gauge.call_args_list}
    assert "prismal.gateway_quota_remaining" in created
    mgr.set_gauge("gateway_quota_remaining", 3.5, attributes={"gateway": "g", "window": "5h"})
    gauge: Any = mgr._gauges["gateway_quota_remaining"]
    gauge.set.assert_called_once_with(3.5, {"gateway": "g", "window": "5h"})


def test_set_gauge_unknown_or_disabled_is_noop() -> None:
    mgr = object.__new__(OTelManager)
    mgr.set_gauge("nope", 1.0)  # no _gauges attribute at all: must not raise
    mgr2 = _registered()
    mgr2._gauges["gateway_quota_remaining"].set.side_effect = RuntimeError("boom")
    mgr2.set_gauge("gateway_quota_remaining", 1.0)  # swallowed
