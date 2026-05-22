"""Unit tests for MCPClientManager.

All network I/O and MCP SDK calls are mocked.  Tests cover:
- Initialization with default and custom paths
- Loading config from YAML (enabled/disabled servers, missing file, bad YAML)
- connect() / disconnect() lifecycle
- reload() diffing logic
- get_all_langchain_tools() with lazy adapter import
- get_server_status()
- close()
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prismal.mcp.client import MCPClientManager
from prismal.mcp.connection import MCPServerConfig, MCPServerStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STDIO_ENTRY = {
    "name": "test-server",
    "type": "stdio",
    "command": ["python", "-m", "testserver"],
    "enabled": True,
    "timeout_seconds": 5,
}

_STDIO_ENTRY_DISABLED = {
    "name": "disabled-server",
    "type": "stdio",
    "command": ["python", "-m", "disabledserver"],
    "enabled": False,
    "timeout_seconds": 5,
}


def _make_yaml(servers: list[dict]) -> str:  # type: ignore[type-arg]
    """Return a minimal YAML string for the given server dicts."""
    import yaml

    return yaml.dump({"servers": servers})


def _make_connected_mock_conn(
    name: str = "test-server", tool_count: int = 0, priority: int = 0
) -> MagicMock:
    """Return a mock MCPServerConnection that is already connected."""
    conn = MagicMock()
    conn.connected = True
    conn.connect = AsyncMock()
    conn.disconnect = AsyncMock()
    conn.cached_tools = [MagicMock() for _ in range(tool_count)]
    conn._config.priority = priority  # required by get_all_langchain_tools priority sort
    conn.status = MCPServerStatus(
        name=name,
        connected=True,
        tool_count=tool_count,
        error=None,
        server_type="stdio",
        priority=priority,
    )
    return conn


def _make_disconnected_mock_conn(name: str = "test-server") -> MagicMock:
    """Return a mock MCPServerConnection that failed to connect."""
    conn = MagicMock()
    conn.connected = False
    conn.connect = AsyncMock()
    conn.disconnect = AsyncMock()
    conn.cached_tools = []
    conn.status = MCPServerStatus(
        name=name,
        connected=False,
        tool_count=0,
        error="Connection refused",
        server_type="stdio",
    )
    return conn


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestMCPClientManagerInit:
    """Tests for MCPClientManager initialisation."""

    def test_default_config_path(self) -> None:
        """Manager must use the default config path when none is supplied."""
        manager = MCPClientManager()
        assert manager._config_path == Path("config/mcp_servers.yaml")

    def test_custom_config_path(self, tmp_path: Path) -> None:
        """Manager must store a custom config path when supplied."""
        custom = tmp_path / "custom_mcp.yaml"
        manager = MCPClientManager(config_path=custom)
        assert manager._config_path == custom

    def test_initial_connections_empty(self) -> None:
        """The connections registry must be empty on init."""
        manager = MCPClientManager()
        assert manager._connections == {}

    def test_initial_configs_empty(self) -> None:
        """The configs registry must be empty on init."""
        manager = MCPClientManager()
        assert manager._configs == {}


# ---------------------------------------------------------------------------
# load_from_config
# ---------------------------------------------------------------------------


class TestLoadFromConfig:
    """Tests for MCPClientManager.load_from_config()."""

    @pytest.mark.asyncio
    async def test_connects_enabled_servers(self, tmp_path: Path) -> None:
        """load_from_config must call connect() for each enabled server."""
        cfg_file = tmp_path / "mcp_servers.yaml"
        cfg_file.write_text(_make_yaml([_STDIO_ENTRY]))

        manager = MCPClientManager(config_path=cfg_file)

        mock_conn = _make_connected_mock_conn()
        with patch(
            "lightagent.mcp.client.MCPServerConnection",
            return_value=mock_conn,
        ):
            await manager.load_from_config()

        mock_conn.connect.assert_awaited_once()
        assert "test-server" in manager._connections

    @pytest.mark.asyncio
    async def test_skips_disabled_servers(self, tmp_path: Path) -> None:
        """load_from_config must not call connect() for disabled servers."""
        cfg_file = tmp_path / "mcp_servers.yaml"
        cfg_file.write_text(_make_yaml([_STDIO_ENTRY_DISABLED]))

        manager = MCPClientManager(config_path=cfg_file)

        mock_conn = _make_connected_mock_conn()
        with patch(
            "lightagent.mcp.client.MCPServerConnection",
            return_value=mock_conn,
        ):
            await manager.load_from_config()

        mock_conn.connect.assert_not_awaited()
        assert "disabled-server" not in manager._connections

    @pytest.mark.asyncio
    async def test_mixed_enabled_disabled(self, tmp_path: Path) -> None:
        """load_from_config must connect enabled servers and skip disabled ones."""
        cfg_file = tmp_path / "mcp_servers.yaml"
        cfg_file.write_text(_make_yaml([_STDIO_ENTRY, _STDIO_ENTRY_DISABLED]))

        manager = MCPClientManager(config_path=cfg_file)

        connected_mock = _make_connected_mock_conn("test-server")
        disabled_mock = _make_connected_mock_conn("disabled-server")

        def _conn_factory(cfg: MCPServerConfig) -> MagicMock:
            if cfg.name == "test-server":
                return connected_mock
            return disabled_mock

        with patch(
            "lightagent.mcp.client.MCPServerConnection",
            side_effect=_conn_factory,
        ):
            await manager.load_from_config()

        connected_mock.connect.assert_awaited_once()
        disabled_mock.connect.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_config_file_no_crash(self, tmp_path: Path) -> None:
        """load_from_config must not crash when config file does not exist."""
        manager = MCPClientManager(config_path=tmp_path / "nonexistent.yaml")
        # Must complete without raising
        await manager.load_from_config()
        assert manager._connections == {}

    @pytest.mark.asyncio
    async def test_malformed_yaml_no_crash(self, tmp_path: Path) -> None:
        """load_from_config must not crash on malformed YAML."""
        cfg_file = tmp_path / "bad.yaml"
        cfg_file.write_text("servers: [unterminated: {bad yaml")

        manager = MCPClientManager(config_path=cfg_file)
        # Must complete without raising
        await manager.load_from_config()
        assert manager._connections == {}

    @pytest.mark.asyncio
    async def test_config_path_override(self, tmp_path: Path) -> None:
        """load_from_config must use the config_path argument when provided."""
        default_path = tmp_path / "default.yaml"
        override_path = tmp_path / "override.yaml"

        # Only the override file has content.
        override_path.write_text(_make_yaml([_STDIO_ENTRY]))

        manager = MCPClientManager(config_path=default_path)

        mock_conn = _make_connected_mock_conn()
        with patch(
            "lightagent.mcp.client.MCPServerConnection",
            return_value=mock_conn,
        ):
            await manager.load_from_config(config_path=override_path)

        assert "test-server" in manager._connections

    @pytest.mark.asyncio
    async def test_invalid_server_entry_skipped(self, tmp_path: Path) -> None:
        """load_from_config must skip entries that fail Pydantic validation."""
        bad_entry = {"name": "bad", "type": "stdio"}  # missing 'command'
        good_entry = _STDIO_ENTRY
        cfg_file = tmp_path / "mcp_servers.yaml"
        cfg_file.write_text(_make_yaml([bad_entry, good_entry]))

        manager = MCPClientManager(config_path=cfg_file)

        mock_conn = _make_connected_mock_conn()
        with patch(
            "lightagent.mcp.client.MCPServerConnection",
            return_value=mock_conn,
        ):
            await manager.load_from_config()

        # Only the valid entry should be connected.
        assert "test-server" in manager._connections
        assert "bad" not in manager._connections

    @pytest.mark.asyncio
    async def test_empty_servers_list(self, tmp_path: Path) -> None:
        """load_from_config on a YAML with an empty servers list must not crash."""
        cfg_file = tmp_path / "mcp_servers.yaml"
        cfg_file.write_text("servers: []\n")

        manager = MCPClientManager(config_path=cfg_file)
        await manager.load_from_config()
        assert manager._connections == {}


# ---------------------------------------------------------------------------
# connect / disconnect
# ---------------------------------------------------------------------------


class TestConnectDisconnect:
    """Tests for connect() and disconnect() methods."""

    @pytest.mark.asyncio
    async def test_connect_creates_connection_and_calls_connect(self) -> None:
        """connect() must instantiate MCPServerConnection and call connect()."""
        cfg = MCPServerConfig.model_validate(_STDIO_ENTRY)
        manager = MCPClientManager()

        mock_conn = _make_connected_mock_conn()
        with patch(
            "lightagent.mcp.client.MCPServerConnection",
            return_value=mock_conn,
        ):
            await manager.connect(cfg)

        mock_conn.connect.assert_awaited_once()
        assert manager._connections["test-server"] is mock_conn

    @pytest.mark.asyncio
    async def test_connect_replaces_existing_connection(self) -> None:
        """connect() must disconnect any existing connection for the same server."""
        cfg = MCPServerConfig.model_validate(_STDIO_ENTRY)
        manager = MCPClientManager()

        old_conn = _make_connected_mock_conn()
        new_conn = _make_connected_mock_conn()

        manager._connections["test-server"] = old_conn

        with patch(
            "lightagent.mcp.client.MCPServerConnection",
            return_value=new_conn,
        ):
            await manager.connect(cfg)

        old_conn.disconnect.assert_awaited_once()
        assert manager._connections["test-server"] is new_conn

    @pytest.mark.asyncio
    async def test_disconnect_removes_connection(self) -> None:
        """disconnect() must call disconnect on the connection and remove it."""
        manager = MCPClientManager()
        mock_conn = _make_connected_mock_conn()
        manager._connections["test-server"] = mock_conn

        await manager.disconnect("test-server")

        mock_conn.disconnect.assert_awaited_once()
        assert "test-server" not in manager._connections

    @pytest.mark.asyncio
    async def test_disconnect_unknown_server_no_crash(self) -> None:
        """disconnect() must not raise when the server name is unknown."""
        manager = MCPClientManager()
        # Must complete without raising
        await manager.disconnect("nonexistent-server")


# ---------------------------------------------------------------------------
# reload
# ---------------------------------------------------------------------------


class TestReload:
    """Tests for reload() hot-reload logic."""

    @pytest.mark.asyncio
    async def test_reload_connects_new_servers(self, tmp_path: Path) -> None:
        """reload() must connect servers that appear in the new config."""
        cfg_file = tmp_path / "mcp_servers.yaml"
        cfg_file.write_text(_make_yaml([_STDIO_ENTRY]))

        manager = MCPClientManager(config_path=cfg_file)

        mock_conn = _make_connected_mock_conn()
        with patch(
            "lightagent.mcp.client.MCPServerConnection",
            return_value=mock_conn,
        ):
            await manager.reload()

        assert "test-server" in manager._connections
        mock_conn.connect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reload_keeps_existing_connections(self, tmp_path: Path) -> None:
        """reload() must not disconnect servers already in the config."""
        cfg_file = tmp_path / "mcp_servers.yaml"
        cfg_file.write_text(_make_yaml([_STDIO_ENTRY]))

        manager = MCPClientManager(config_path=cfg_file)
        existing_conn = _make_connected_mock_conn()
        manager._connections["test-server"] = existing_conn
        manager._configs["test-server"] = MCPServerConfig.model_validate(_STDIO_ENTRY)

        with patch(
            "lightagent.mcp.client.MCPServerConnection",
            return_value=_make_connected_mock_conn(),
        ):
            await manager.reload()

        # The existing connection must still be the one in the registry.
        existing_conn.disconnect.assert_not_awaited()
        assert manager._connections["test-server"] is existing_conn

    @pytest.mark.asyncio
    async def test_reload_disconnects_removed_servers(self, tmp_path: Path) -> None:
        """reload() must disconnect servers no longer present in the config."""
        cfg_file = tmp_path / "mcp_servers.yaml"
        # New config has no servers at all.
        cfg_file.write_text("servers: []\n")

        manager = MCPClientManager(config_path=cfg_file)
        old_conn = _make_connected_mock_conn("old-server")
        manager._connections["old-server"] = old_conn
        manager._configs["old-server"] = MCPServerConfig.model_validate(
            {**_STDIO_ENTRY, "name": "old-server"}
        )

        await manager.reload()

        old_conn.disconnect.assert_awaited_once()
        assert "old-server" not in manager._connections

    @pytest.mark.asyncio
    async def test_reload_disconnects_newly_disabled_server(self, tmp_path: Path) -> None:
        """reload() must disconnect a server that is now disabled in the config."""
        cfg_file = tmp_path / "mcp_servers.yaml"
        cfg_file.write_text(_make_yaml([_STDIO_ENTRY_DISABLED]))

        manager = MCPClientManager(config_path=cfg_file)
        old_conn = _make_connected_mock_conn("disabled-server")
        manager._connections["disabled-server"] = old_conn
        manager._configs["disabled-server"] = MCPServerConfig.model_validate(
            {**_STDIO_ENTRY, "name": "disabled-server"}
        )

        await manager.reload()

        old_conn.disconnect.assert_awaited_once()
        assert "disabled-server" not in manager._connections

    @pytest.mark.asyncio
    async def test_reload_missing_config_no_crash(self, tmp_path: Path) -> None:
        """reload() must not crash when the config file is missing."""
        manager = MCPClientManager(config_path=tmp_path / "missing.yaml")
        # Pre-populate a connection — it must be left untouched.
        existing_conn = _make_connected_mock_conn()
        manager._connections["test-server"] = existing_conn

        await manager.reload()

        # Existing connection must not be disturbed.
        existing_conn.disconnect.assert_not_awaited()


# ---------------------------------------------------------------------------
# get_all_langchain_tools
# ---------------------------------------------------------------------------


class TestGetAllLangchainTools:
    """Tests for get_all_langchain_tools()."""

    def test_returns_empty_list_when_no_connections(self) -> None:
        """get_all_langchain_tools() must return [] when no servers are connected."""
        manager = MCPClientManager()
        tools = manager.get_all_langchain_tools()
        assert tools == []

    def test_returns_empty_list_when_all_disconnected(self) -> None:
        """get_all_langchain_tools() must skip disconnected connections."""
        manager = MCPClientManager()
        disconnected = _make_disconnected_mock_conn()
        manager._connections["test-server"] = disconnected

        mock_adapter_cls = MagicMock()
        with patch.dict(
            "sys.modules",
            {"lightagent.mcp.adapter": MagicMock(MCPToolAdapter=mock_adapter_cls)},
        ):
            tools = manager.get_all_langchain_tools()

        assert tools == []
        mock_adapter_cls.assert_not_called()

    def test_returns_tools_from_connected_servers(self) -> None:
        """get_all_langchain_tools() must return one BaseTool per (server, tool)
        pair."""
        manager = MCPClientManager()
        conn = _make_connected_mock_conn(tool_count=2)
        manager._connections["test-server"] = conn

        fake_tool_a = MagicMock()
        fake_tool_b = MagicMock()
        mock_adapter_cls = MagicMock(side_effect=[fake_tool_a, fake_tool_b])

        with patch.dict(
            "sys.modules",
            {"lightagent.mcp.adapter": MagicMock(MCPToolAdapter=mock_adapter_cls)},
        ):
            tools = manager.get_all_langchain_tools()

        assert len(tools) == 2
        assert tools[0] is fake_tool_a
        assert tools[1] is fake_tool_b

    def test_returns_empty_list_when_adapter_import_fails(self) -> None:
        """get_all_langchain_tools() must return [] when MCPToolAdapter is not
        available."""
        manager = MCPClientManager()
        conn = _make_connected_mock_conn(tool_count=1)
        manager._connections["test-server"] = conn

        with patch.dict("sys.modules", {"lightagent.mcp.adapter": None}):
            tools = manager.get_all_langchain_tools()

        assert tools == []

    def test_aggregates_tools_from_multiple_servers(self) -> None:
        """get_all_langchain_tools() must combine tools from all connected servers."""
        manager = MCPClientManager()
        conn_a = _make_connected_mock_conn("server-a", tool_count=2)
        conn_b = _make_connected_mock_conn("server-b", tool_count=3)
        manager._connections["server-a"] = conn_a
        manager._connections["server-b"] = conn_b

        mock_adapter_cls = MagicMock(side_effect=[MagicMock() for _ in range(5)])

        with patch.dict(
            "sys.modules",
            {"lightagent.mcp.adapter": MagicMock(MCPToolAdapter=mock_adapter_cls)},
        ):
            tools = manager.get_all_langchain_tools()

        assert len(tools) == 5


# ---------------------------------------------------------------------------
# get_server_status
# ---------------------------------------------------------------------------


class TestGetServerStatus:
    """Tests for get_server_status()."""

    def test_returns_empty_list_when_no_connections(self) -> None:
        """get_server_status() must return [] when no connections are registered."""
        manager = MCPClientManager()
        assert manager.get_server_status() == []

    def test_returns_status_for_connected_server(self) -> None:
        """get_server_status() must include the status of connected servers."""
        manager = MCPClientManager()
        conn = _make_connected_mock_conn("my-server")
        manager._connections["my-server"] = conn

        statuses = manager.get_server_status()

        assert len(statuses) == 1
        assert statuses[0].name == "my-server"
        assert statuses[0].connected is True

    def test_returns_status_for_disconnected_server(self) -> None:
        """get_server_status() must include the status of unavailable servers."""
        manager = MCPClientManager()
        conn = _make_disconnected_mock_conn("bad-server")
        manager._connections["bad-server"] = conn

        statuses = manager.get_server_status()

        assert len(statuses) == 1
        assert statuses[0].connected is False
        assert statuses[0].error == "Connection refused"

    def test_returns_status_for_multiple_servers(self) -> None:
        """get_server_status() must return one status per registered connection."""
        manager = MCPClientManager()
        manager._connections["server-a"] = _make_connected_mock_conn("server-a")
        manager._connections["server-b"] = _make_disconnected_mock_conn("server-b")

        statuses = manager.get_server_status()
        assert len(statuses) == 2


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


class TestClose:
    """Tests for close()."""

    @pytest.mark.asyncio
    async def test_close_disconnects_all_servers(self) -> None:
        """close() must call disconnect() on every registered connection."""
        manager = MCPClientManager()
        conn_a = _make_connected_mock_conn("server-a")
        conn_b = _make_connected_mock_conn("server-b")
        manager._connections["server-a"] = conn_a
        manager._connections["server-b"] = conn_b

        await manager.close()

        conn_a.disconnect.assert_awaited_once()
        conn_b.disconnect.assert_awaited_once()
        assert manager._connections == {}

    @pytest.mark.asyncio
    async def test_close_with_no_connections_no_crash(self) -> None:
        """close() must not crash when there are no registered connections."""
        manager = MCPClientManager()
        await manager.close()
        assert manager._connections == {}
