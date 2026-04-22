"""Unit tests for Fase E — MCP capability routing.

Exercises:
- ``MCPClientManager.get_all_langchain_tools(capabilities=...)`` filtering.
- ``get_tools_for_agent(..., required_capabilities=...)`` plumbing.
- Backward-compat: ``capabilities=None`` returns the full pool.
- Universal capability: servers tagged ``["general"]`` always included.
- YAML default: entries without ``capabilities`` fall back to ``["general"]``.

No real MCP server is required — ``MCPServerConnection`` is mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lightagent.mcp.client import MCPClientManager
from lightagent.mcp.connection import MCPServerConfig

pytestmark = pytest.mark.unit


# ── Helpers ───────────────────────────────────────────────────────────────────


def _mock_conn(
    name: str,
    capabilities: list[str],
    tool_count: int = 1,
    priority: int = 0,
) -> MagicMock:
    """Return a connected MCPServerConnection mock tagged with *capabilities*."""
    conn = MagicMock()
    conn.connected = True
    conn.connect = AsyncMock()
    conn.disconnect = AsyncMock()
    # Tool adapters iterate ``cached_tools``; give each a name for debugging.
    conn.cached_tools = [MagicMock(name=f"{name}-tool-{i}") for i in range(tool_count)]
    conn._config.priority = priority
    conn._config.capabilities = capabilities
    conn._config.name = name
    return conn


def _manager_with_conns(conns: list[MagicMock]) -> MCPClientManager:
    mgr = MCPClientManager()
    for c in conns:
        mgr._connections[c._config.name] = c
    return mgr


def _adapter_passthrough():  # type: ignore[no-untyped-def]
    """Patch ``MCPToolAdapter`` to return its own MCP tool — simpler to count."""
    from lightagent.mcp import adapter as adapter_module

    def _fake_adapter(conn, tool):  # type: ignore[no-untyped-def]
        stub = MagicMock()
        stub.name = getattr(tool, "name", "tool")
        stub.description = ""
        # Attach the source server for assertions.
        stub.server_name = conn._config.name
        return stub

    return patch.object(adapter_module, "MCPToolAdapter", side_effect=_fake_adapter)


# ── MCPServerConfig default capability ───────────────────────────────────────


def test_server_config_defaults_to_general_capability() -> None:
    """Legacy configs without ``capabilities`` are treated as ``['general']``."""
    cfg = MCPServerConfig(
        name="legacy",
        type="stdio",
        command=["python", "-m", "dummy"],
    )
    assert cfg.capabilities == ["general"]


def test_server_config_accepts_explicit_capabilities() -> None:
    cfg = MCPServerConfig(
        name="code_scanner",
        type="stdio",
        command=["ruff"],
        capabilities=["code_review", "code_execution"],
    )
    assert set(cfg.capabilities) == {"code_review", "code_execution"}


# ── Filter: capabilities=None returns everything ─────────────────────────────


def test_get_all_langchain_tools_no_filter_returns_all() -> None:
    conns = [
        _mock_conn("cs", capabilities=["customer_service"], tool_count=2),
        _mock_conn("cr", capabilities=["code_review"], tool_count=3),
        _mock_conn("rs", capabilities=["research"], tool_count=1),
    ]
    mgr = _manager_with_conns(conns)
    with _adapter_passthrough():
        tools = mgr.get_all_langchain_tools(capabilities=None)
    # 2 + 3 + 1 = 6 tools when no filter is applied.
    assert len(tools) == 6


# ── Filter: positive / negative match ────────────────────────────────────────


def test_filter_includes_matching_server_excludes_others() -> None:
    conns = [
        _mock_conn("cs", capabilities=["customer_service"], tool_count=2),
        _mock_conn("cr", capabilities=["code_review"], tool_count=3),
    ]
    mgr = _manager_with_conns(conns)
    with _adapter_passthrough():
        code_tools = mgr.get_all_langchain_tools(capabilities=["code_review"])
    assert len(code_tools) == 3
    assert all(t.server_name == "cr" for t in code_tools)


def test_filter_empty_result_when_no_server_matches() -> None:
    conns = [
        _mock_conn("cs", capabilities=["customer_service"], tool_count=2),
    ]
    mgr = _manager_with_conns(conns)
    with _adapter_passthrough():
        tools = mgr.get_all_langchain_tools(capabilities=["code_review"])
    assert tools == []


# ── Universal capability ─────────────────────────────────────────────────────


def test_general_servers_always_included() -> None:
    """A server with ``['general']`` slips past any filter — it's universal."""
    conns = [
        _mock_conn("gen", capabilities=["general"], tool_count=1),
        _mock_conn("cs", capabilities=["customer_service"], tool_count=2),
    ]
    mgr = _manager_with_conns(conns)
    with _adapter_passthrough():
        tools = mgr.get_all_langchain_tools(capabilities=["code_review"])
    # "gen" is always kept; "cs" doesn't match → 1 tool.
    assert len(tools) == 1
    assert tools[0].server_name == "gen"


def test_general_and_matching_both_included() -> None:
    conns = [
        _mock_conn("gen", capabilities=["general"], tool_count=1),
        _mock_conn("cr", capabilities=["code_review"], tool_count=2),
    ]
    mgr = _manager_with_conns(conns)
    with _adapter_passthrough():
        tools = mgr.get_all_langchain_tools(capabilities=["code_review"])
    names = {t.server_name for t in tools}
    assert names == {"gen", "cr"}
    assert len(tools) == 3


# ── Integration: get_tools_for_agent plumbing ────────────────────────────────


def test_get_tools_for_agent_forwards_capabilities_to_manager() -> None:
    from lightagent.agents import tool_registry

    captured: dict[str, object] = {}

    class FakeMgr:
        def get_all_langchain_tools(self, capabilities=None):  # type: ignore[no-untyped-def]
            captured["caps"] = capabilities
            return []

    with (
        patch.object(tool_registry, "_mcp_manager", FakeMgr()),
        patch.object(tool_registry, "get_skill_tools", return_value=[]),
        patch(
            "lightagent.mcp.client.MCPClientManager",
            FakeMgr,
        ),
    ):
        tool_registry.get_tools_for_agent("researcher", required_capabilities=["research", "rag"])

    assert captured["caps"] == ["research", "rag"]


def test_get_tools_for_agent_legacy_call_passes_none() -> None:
    """Callers that omit ``required_capabilities`` still trigger the legacy path."""
    from lightagent.agents import tool_registry

    captured: dict[str, object] = {"caps": "UNSET"}

    class FakeMgr:
        def get_all_langchain_tools(self, capabilities=None):  # type: ignore[no-untyped-def]
            captured["caps"] = capabilities
            return []

    with (
        patch.object(tool_registry, "_mcp_manager", FakeMgr()),
        patch.object(tool_registry, "get_skill_tools", return_value=[]),
        patch("lightagent.mcp.client.MCPClientManager", FakeMgr),
    ):
        tool_registry.get_tools_for_agent("researcher")

    assert captured["caps"] is None


# ── Recommended mapping (E4) ─────────────────────────────────────────────────


def test_default_capability_map_has_all_new_nodes() -> None:
    """Mapping covers the 4 Fase-B patterns + 5 Fase-C subgraphs from the prompt."""
    from lightagent.agents.tool_registry import DEFAULT_CAPABILITY_MAP

    expected = {
        "tot_agent",
        "lats_agent",
        "llm_compiler",
        "mixture_agent",
        "customer_service",
        "code_review",
        "data_etl",
        "document_generation",
        "debate_consensus",
    }
    assert expected.issubset(DEFAULT_CAPABILITY_MAP.keys())


def test_get_recommended_capabilities_unknown_returns_none() -> None:
    """Legacy agents not in the map get the full pool (None)."""
    from lightagent.agents.tool_registry import get_recommended_capabilities

    assert get_recommended_capabilities("researcher") is None
    assert get_recommended_capabilities("coder") is None


def test_get_recommended_capabilities_new_nodes_return_lists() -> None:
    from lightagent.agents.tool_registry import get_recommended_capabilities

    assert get_recommended_capabilities("code_review") == [
        "code_review",
        "code_execution",
        "file_management",
    ]
    assert "customer_service" in (get_recommended_capabilities("customer_service") or [])


def test_get_tools_for_agent_code_review_excludes_customer_service_tools() -> None:
    """End-to-end: code_review filter + fake servers → no customer_service tools."""
    from lightagent.agents import tool_registry

    conns = [
        _mock_conn("cs", capabilities=["customer_service"], tool_count=2),
        _mock_conn("cr", capabilities=["code_review"], tool_count=3),
    ]
    mgr = _manager_with_conns(conns)

    with (
        patch.object(tool_registry, "_mcp_manager", mgr),
        patch.object(tool_registry, "get_skill_tools", return_value=[]),
        _adapter_passthrough(),
    ):
        tools = tool_registry.get_tools_for_agent(
            "code_review", required_capabilities=["code_review"]
        )

    # Only tools from the code_review server should surface.
    mcp_tools = [t for t in tools if hasattr(t, "server_name")]
    assert all(t.server_name == "cr" for t in mcp_tools)
    assert len(mcp_tools) == 3
