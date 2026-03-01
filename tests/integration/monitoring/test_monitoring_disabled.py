"""Integration test: graceful no-op when monitoring is disabled (T-13A)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _disabled_settings() -> MagicMock:
    """Return a mock settings object with all monitoring disabled."""
    s = MagicMock()
    s.langfuse_enabled = False
    s.langfuse_public_key.get_secret_value.return_value = ""
    s.langfuse_secret_key.get_secret_value.return_value = ""
    s.otel_enabled = False
    return s


def test_langfuse_manager_disabled_no_crash() -> None:
    """LangfuseManager with all monitoring off does not crash on any method."""
    from lightagent.monitoring.langfuse_client import LangfuseManager

    LangfuseManager._instance = None
    LangfuseManager._initialized = False
    with patch(
        "lightagent.monitoring._settings_proxy.get_monitoring_settings",
        return_value=_disabled_settings(),
    ):
        mgr = LangfuseManager()

    assert not mgr.enabled

    # All methods must be safe no-ops
    trace = mgr.create_trace(name="test", session_id="s1", user_id="u1")
    assert trace is not None  # _NoOpTrace

    handler = mgr.get_callback_handler(trace_id="t1")
    assert handler is None

    mgr.score_trace("t1", "relevance", 0.9)  # no exception
    mgr.flush()  # no exception
    mgr.shutdown()  # no exception


def test_otel_manager_disabled_no_crash() -> None:
    """OTelManager with OTEL disabled does not crash on any method."""
    from lightagent.monitoring.otel import OTelManager, _NoOpSpan

    OTelManager._instance = None
    OTelManager._initialized = False
    with patch(
        "lightagent.monitoring._settings_proxy.get_monitoring_settings",
        return_value=_disabled_settings(),
    ):
        otel = OTelManager()

    assert not otel.enabled

    with otel.start_span("test.op", attributes={"foo": "bar"}) as span:
        assert isinstance(span, _NoOpSpan)
        span.set_attribute("key", "value")
        span.add_event("my_event")
        span.record_exception(ValueError("test"))

    otel.increment_counter("llm_requests", 5)
    otel.record_histogram("agent_latency", 0.42)
    otel.shutdown()


def test_both_managers_disabled_coexist() -> None:
    """Both LangfuseManager and OTelManager can be disabled simultaneously."""
    from lightagent.monitoring.langfuse_client import LangfuseManager
    from lightagent.monitoring.otel import OTelManager

    LangfuseManager._instance = None
    LangfuseManager._initialized = False
    OTelManager._instance = None
    OTelManager._initialized = False

    disabled = _disabled_settings()
    with patch(
        "lightagent.monitoring._settings_proxy.get_monitoring_settings",
        return_value=disabled,
    ):
        langfuse = LangfuseManager()
        otel = OTelManager()

    assert not langfuse.enabled
    assert not otel.enabled

    # Simulate what a monitored function would do
    trace = langfuse.create_trace(name="agent.run", session_id="sess-1")
    with otel.start_span("agent.execute") as span:
        span.set_attribute("lightagent.model", "gpt-4o")
        otel.increment_counter("llm_requests")
        handler = langfuse.get_callback_handler()
        assert handler is None


def test_monitoring_import_without_env() -> None:
    """The monitoring package imports cleanly without any env vars set."""
    import lightagent.monitoring as monitoring

    assert hasattr(monitoring, "LangfuseManager")
    assert hasattr(monitoring, "OTelManager")
