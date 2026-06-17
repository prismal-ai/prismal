"""Identity OTel counters (Phase IDN — SPEC-IDN-OTEL-001 / ID6-05)."""

from __future__ import annotations

from unittest.mock import MagicMock

from prismal.monitoring.otel import OTelManager

_EXPECTED = ("identity_issued", "policy_decisions", "credential_resolved", "did_verify")


def test_identity_counters_are_registered() -> None:
    mgr = OTelManager.__new__(OTelManager)
    mgr._meter = MagicMock()
    mgr._counters = {}
    mgr._histograms = {}
    mgr._register_standard_metrics()
    for key in _EXPECTED:
        assert key in mgr._counters
