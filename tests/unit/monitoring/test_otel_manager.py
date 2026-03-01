"""Tests for OTelManager singleton (T-132)."""

from __future__ import annotations

from unittest.mock import patch

from lightagent.monitoring.otel import OTelManager, _NoOpSpan


def _reset_singleton() -> None:
    OTelManager._instance = None
    OTelManager._initialized = False


def test_otel_manager_is_singleton() -> None:
    """Two calls return the same instance."""
    _reset_singleton()
    a = OTelManager()
    b = OTelManager()
    assert a is b


def test_start_span_returns_noop_when_disabled() -> None:
    """start_span returns a _NoOpSpan context manager when OTEL disabled."""
    _reset_singleton()
    with patch(
        "lightagent.monitoring._settings_proxy.get_monitoring_settings"
    ) as mock_settings:
        from unittest.mock import MagicMock

        s = MagicMock()
        s.otel_enabled = False
        mock_settings.return_value = s
        mgr = OTelManager()
    assert not mgr.enabled
    with mgr.start_span("test.op") as span:
        assert isinstance(span, _NoOpSpan)
        span.set_attribute("key", "value")  # must not raise


def test_noop_span_methods() -> None:
    """All _NoOpSpan methods are callable without error."""
    span = _NoOpSpan()
    span.set_attribute("foo", "bar")
    span.add_event("my_event", {"k": "v"})
    span.record_exception(ValueError("test"))
    span.set_status(None)


def test_increment_counter_noop_when_disabled() -> None:
    """increment_counter is safe no-op when OTEL disabled."""
    _reset_singleton()
    with patch(
        "lightagent.monitoring._settings_proxy.get_monitoring_settings"
    ) as mock_settings:
        from unittest.mock import MagicMock

        s = MagicMock()
        s.otel_enabled = False
        mock_settings.return_value = s
        mgr = OTelManager()
    mgr.increment_counter("llm_requests")  # must not raise


def test_record_histogram_noop_when_disabled() -> None:
    """record_histogram is safe no-op when OTEL disabled."""
    _reset_singleton()
    with patch(
        "lightagent.monitoring._settings_proxy.get_monitoring_settings"
    ) as mock_settings:
        from unittest.mock import MagicMock

        s = MagicMock()
        s.otel_enabled = False
        mock_settings.return_value = s
        mgr = OTelManager()
    mgr.record_histogram("agent_latency", 0.5)  # must not raise


def test_start_span_with_attributes_noop() -> None:
    """start_span accepts attributes when disabled."""
    _reset_singleton()
    with patch(
        "lightagent.monitoring._settings_proxy.get_monitoring_settings"
    ) as mock_settings:
        from unittest.mock import MagicMock

        s = MagicMock()
        s.otel_enabled = False
        mock_settings.return_value = s
        mgr = OTelManager()
    with mgr.start_span("op", attributes={"foo": "bar"}) as span:
        assert isinstance(span, _NoOpSpan)
