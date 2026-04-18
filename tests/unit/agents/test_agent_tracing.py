"""Tests for agent monitoring instrumentation (T-134)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from lightagent.monitoring.otel import OTelManager, _NoOpSpan


def test_otel_manager_start_span_context_manager() -> None:
    """start_span works as a context manager returning a span."""
    OTelManager._instance = None
    OTelManager._initialized = False
    with patch("lightagent.monitoring._settings_proxy.get_monitoring_settings") as mock_settings:
        s = MagicMock()
        s.otel_enabled = False
        mock_settings.return_value = s
        otel = OTelManager()
    with otel.start_span("agent.test") as span:
        assert isinstance(span, _NoOpSpan)
        span.set_attribute("lightagent.agent", "supervisor")


def test_langfuse_manager_callback_none_when_disabled() -> None:
    """get_callback_handler returns None when Langfuse disabled."""
    from lightagent.monitoring.langfuse_client import LangfuseManager

    LangfuseManager._instance = None
    LangfuseManager._initialized = False
    with patch("lightagent.monitoring._settings_proxy.get_monitoring_settings") as mock_settings:
        s = MagicMock()
        s.langfuse_enabled = False
        s.langfuse_public_key.get_secret_value.return_value = ""
        s.langfuse_secret_key.get_secret_value.return_value = ""
        mock_settings.return_value = s
        mgr = LangfuseManager()
    assert mgr.get_callback_handler() is None
