"""Tests for MCP client monitoring instrumentation (T-137)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lightagent.monitoring.otel import OTelManager, _NoOpSpan


def test_mcp_span_attributes() -> None:
    """MCP spans support standard lightagent.mcp attributes."""
    OTelManager._instance = None
    OTelManager._initialized = False
    with patch("lightagent.monitoring._settings_proxy.get_monitoring_settings") as mock_settings:
        s = MagicMock()
        s.otel_enabled = False
        mock_settings.return_value = s
        otel = OTelManager()
    with otel.start_span("mcp.tool_call") as span:
        assert isinstance(span, _NoOpSpan)
        span.set_attribute("lightagent.mcp.server_name", "filesystem")
        span.set_attribute("lightagent.mcp.tool_name", "read_file")
        span.set_attribute("lightagent.mcp.input_len", 42)


def test_mcp_counter_increments() -> None:
    """mcp_tool_calls counter increments without error."""
    OTelManager._instance = None
    OTelManager._initialized = False
    with patch("lightagent.monitoring._settings_proxy.get_monitoring_settings") as mock_settings:
        s = MagicMock()
        s.otel_enabled = False
        mock_settings.return_value = s
        otel = OTelManager()
    otel.increment_counter(
        "mcp_tool_calls", attributes={"server": "filesystem", "tool": "read_file"}
    )


def test_mcp_adapter_imports_correctly() -> None:
    """MCP adapter module imports without error."""
    try:
        import lightagent.mcp.adapter as _  # noqa: F401
    except ImportError:
        pytest.skip("MCP adapter dependencies not available")
