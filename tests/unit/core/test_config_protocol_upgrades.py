"""Protocol-upgrade + host-surface settings, exceptions and counters (PRT CFG/ERR/OTEL, HRS)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from prismal.core.config import Settings
from prismal.core.exceptions import (
    A2AError,
    A2AOAuthError,
    A2AVersionError,
    AgUiTranslationError,
    McpDiscoveryError,
    McpProtocolError,
    McpTaskError,
    McpTaskTimeout,
    PrismalError,
    ProtocolUpgradeError,
)
from prismal.monitoring.otel import OTelManager


def test_defaults_match_spec() -> None:
    s = Settings()
    assert s.a2a_protocol_version == "0.3"
    assert s.a2a_accept_legacy is True
    assert s.a2a_tasks_list_max_page_size == 200
    assert s.a2a_inbound_auth_schemes == []
    assert s.a2a_oauth_redirect_uri is None
    assert s.mcp_protocol_mode == "legacy"
    assert s.mcp_stateless_protocol_version == "2026-07-28"
    assert s.mcp_tool_cache_enabled is False
    assert s.mcp_tool_cache_default_ttl_s == 300
    assert s.mcp_tool_cache_max_ttl_s == 86_400
    assert s.mcp_tool_cache_max_entries == 256
    assert s.mcp_tasks_enabled is False
    assert s.mcp_task_poll_interval_s == 1.0
    assert s.mcp_task_poll_max_interval_s == 15.0
    assert s.mcp_task_timeout_s == 900.0
    assert s.agui_events_enabled is False
    assert s.agui_include_state_deltas is True
    assert s.agui_include_usage is True
    assert s.agui_include_metadata is True
    assert s.host_surface_stats_retention_h == 168


def test_parse_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRISMAL_A2A_PROTOCOL_VERSION", "1.0")
    monkeypatch.setenv("PRISMAL_MCP_PROTOCOL_MODE", "auto")
    monkeypatch.setenv("PRISMAL_A2A_INBOUND_AUTH_SCHEMES", '["oauth2"]')
    s = Settings()
    assert s.a2a_protocol_version == "1.0"
    assert s.mcp_protocol_mode == "auto"
    assert s.a2a_inbound_auth_schemes == ["oauth2"]


def test_default_ttl_above_max_rejected() -> None:
    with pytest.raises(ValidationError, match="MCP_TOOL_CACHE_DEFAULT_TTL_S"):
        Settings(mcp_tool_cache_default_ttl_s=100_000)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"mcp_tool_cache_default_ttl_s": 0},
        {"mcp_task_poll_interval_s": 0.0},
        {"mcp_task_timeout_s": -1.0},
        {"mcp_tool_cache_max_entries": 0},
    ],
)
def test_non_positive_values_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_poll_interval_above_max_rejected() -> None:
    with pytest.raises(ValidationError, match="MCP_TASK_POLL"):
        Settings(mcp_task_poll_interval_s=20.0, mcp_task_poll_max_interval_s=15.0)


def test_legacy_primary_must_accept_legacy() -> None:
    with pytest.raises(ValidationError, match="A2A_ACCEPT_LEGACY"):
        Settings(a2a_protocol_version="0.3", a2a_accept_legacy=False)
    assert Settings(a2a_protocol_version="1.0", a2a_accept_legacy=False).a2a_accept_legacy is False


def test_unknown_values_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(a2a_protocol_version="2.0")
    with pytest.raises(ValidationError):
        Settings(mcp_protocol_mode="magic")


def test_exception_tree() -> None:
    assert issubclass(ProtocolUpgradeError, PrismalError)
    for exc in (A2AVersionError, A2AOAuthError):
        assert issubclass(exc, ProtocolUpgradeError) and issubclass(exc, A2AError)
    for exc in (McpDiscoveryError, McpTaskError):
        assert issubclass(exc, McpProtocolError)
    assert issubclass(McpTaskTimeout, McpTaskError)
    assert issubclass(McpProtocolError, ProtocolUpgradeError)
    assert issubclass(AgUiTranslationError, ProtocolUpgradeError)


def test_protocol_and_host_surface_metrics_registered() -> None:
    mgr = object.__new__(OTelManager)
    mgr._counters = {}
    mgr._histograms = {}
    mgr._meter = MagicMock()
    mgr._register_standard_metrics()
    counters = {c.args[0] for c in mgr._meter.create_counter.call_args_list}
    hists = {c.args[0] for c in mgr._meter.create_histogram.call_args_list}
    expected = {
        "a2a_requests": "prismal.a2a_requests_total",
        "a2a_oauth_tokens": "prismal.a2a_oauth_tokens_total",
        "mcp_protocol_negotiations": "prismal.mcp_protocol_negotiations_total",
        "mcp_tool_cache_hits": "prismal.mcp_tool_cache_hits_total",
        "mcp_tool_cache_misses": "prismal.mcp_tool_cache_misses_total",
        "mcp_tool_list_changes": "prismal.mcp_tool_list_changes_total",
        "mcp_tasks": "prismal.mcp_tasks_total",
        "mcp_input_requests": "prismal.mcp_input_requests_total",
        "agui_events": "prismal.agui_events_total",
        "agui_translation_errors": "prismal.agui_translation_errors_total",
        "host_surface_errors": "prismal.host_surface_errors_total",
    }
    for key, name in expected.items():
        assert key in mgr._counters, key
        assert name in counters, name
    assert "prismal.mcp_task_wait_s" in hists
    assert "mcp_task_wait_s" in mgr._histograms
