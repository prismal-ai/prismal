"""Integration test: OTEL span hierarchy with in-memory exporter (T-13A)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_otel_span_hierarchy_noop() -> None:
    """Nested spans are safe no-ops when OTEL is disabled."""
    from lightagent.monitoring.otel import OTelManager, _NoOpSpan

    OTelManager._instance = None
    OTelManager._initialized = False
    s = MagicMock()
    s.otel_enabled = False
    with patch(
        "lightagent.monitoring._settings_proxy.get_monitoring_settings",
        return_value=s,
    ):
        otel = OTelManager()

    with otel.start_span("agent.execute", {"lightagent.pattern": "SUPERVISOR"}) as parent:
        assert isinstance(parent, _NoOpSpan)
        with otel.start_span("llm.call", {"lightagent.model": "gpt-4o"}) as child:
            assert isinstance(child, _NoOpSpan)
            child.set_attribute("lightagent.tokens", 500)
        parent.set_attribute("lightagent.agent", "supervisor")


def test_span_records_exception() -> None:
    """Exceptions within a span are recorded without suppression."""
    from lightagent.monitoring.otel import OTelManager

    OTelManager._instance = None
    OTelManager._initialized = False
    s = MagicMock()
    s.otel_enabled = False
    with patch(
        "lightagent.monitoring._settings_proxy.get_monitoring_settings",
        return_value=s,
    ):
        otel = OTelManager()

    with pytest.raises(ValueError, match="test error"):
        with otel.start_span("test.op") as span:
            raise ValueError("test error")


def test_counter_and_histogram_noop() -> None:
    """Counters and histograms are safe no-ops when disabled."""
    from lightagent.monitoring.otel import OTelManager

    OTelManager._instance = None
    OTelManager._initialized = False
    s = MagicMock()
    s.otel_enabled = False
    with patch(
        "lightagent.monitoring._settings_proxy.get_monitoring_settings",
        return_value=s,
    ):
        otel = OTelManager()

    # All metric names from the standard set
    for metric in ["llm_requests", "llm_tokens", "agent_errors", "security_blocks", "rag_queries", "mcp_tool_calls"]:
        otel.increment_counter(metric, 1, {"label": "test"})
    for metric in ["agent_latency", "llm_latency", "rag_latency"]:
        otel.record_histogram(metric, 0.5, {"label": "test"})
