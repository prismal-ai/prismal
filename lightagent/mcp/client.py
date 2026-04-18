"""MCP client manager — orchestrates multiple MCP server connections.

Implements ``MCPClientManager`` which loads server configurations from
``config/mcp_servers.yaml``, manages the lifecycle of all
``MCPServerConnection`` instances, and exposes their tools as LangChain
``BaseTool`` instances to the agent graph.

References:
    - SPEC-004 (AC-004-1, AC-004-2, AC-004-3, AC-004-5)
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from lightagent.core.logging import get_logger
from lightagent.mcp.connection import (
    MCPServerConfig,
    MCPServerConnection,
    MCPServerStatus,
)

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

logger = get_logger("lightagent.mcp.client")

# Default location of the MCP server configuration file.
_DEFAULT_CONFIG_PATH = Path("config/mcp_servers.yaml")


class MCPClientManager:
    """Manages the lifecycle of all configured MCP server connections.

    Reads ``config/mcp_servers.yaml`` on startup, creates and connects
    ``MCPServerConnection`` instances for every enabled server, and exposes
    the aggregated tool catalogue to the agent graph.

    Connection errors are logged and the affected server is marked
    unavailable without stopping the manager or other servers (AC-004-5).

    Usage::

        manager = MCPClientManager()
        await manager.load_from_config()
        tools = manager.get_all_langchain_tools()
        statuses = manager.get_server_status()
        await manager.close()
    """

    def __init__(self, config_path: Path | None = None) -> None:
        """Initialise the manager with an optional custom config path.

        Args:
            config_path: Path to the YAML config file.  Defaults to
                ``config/mcp_servers.yaml`` relative to the current working
                directory when ``None``.
        """
        self._config_path: Path = config_path if config_path is not None else _DEFAULT_CONFIG_PATH
        # Keyed by server name — only contains servers that have been connected
        # (or attempted) via ``connect()``.
        self._connections: dict[str, MCPServerConnection] = {}
        # Tracks the last-loaded set of server configs keyed by name so that
        # ``reload()`` can diff old vs new without re-reading every field.
        self._configs: dict[str, MCPServerConfig] = {}

    # ------------------------------------------------------------------
    # Config loading
    # ------------------------------------------------------------------

    async def load_from_config(self, config_path: Path | None = None) -> None:
        """Load and connect all enabled MCP servers from the config file.

        Reads the YAML file at ``config_path`` (or the default path set in
        ``__init__``), parses each entry, and calls ``connect()`` for every
        server where ``enabled=True``.

        Missing or unreadable config files are treated as warnings, not
        errors — the manager will be empty but functional.  Invalid YAML or
        per-server validation errors are also caught and logged.

        Args:
            config_path: Override the config path for this call only.  If
                ``None``, the path supplied to ``__init__`` is used.
        """
        path = config_path if config_path is not None else self._config_path

        if not path.exists():
            logger.warning(
                "mcp_config_not_found",
                path=str(path),
            )
            return

        try:
            raw = path.read_text(encoding="utf-8")
            data = yaml.safe_load(raw)
        except OSError as exc:
            logger.error(
                "mcp_config_read_error",
                path=str(path),
                error=str(exc),
            )
            return
        except yaml.YAMLError as exc:
            logger.error(
                "mcp_config_parse_error",
                path=str(path),
                error=str(exc),
            )
            return

        if not isinstance(data, dict):
            logger.warning(
                "mcp_config_unexpected_structure",
                path=str(path),
            )
            return

        servers_raw: list[object] = data.get("servers") or []
        if not isinstance(servers_raw, list):
            logger.warning(
                "mcp_config_servers_not_a_list",
                path=str(path),
            )
            return

        for entry in servers_raw:
            if not isinstance(entry, dict):
                logger.warning("mcp_config_invalid_entry", entry=str(entry))
                continue
            try:
                cfg = MCPServerConfig.model_validate(entry)
            except Exception as exc:  # broad: pydantic ValidationError or other
                logger.warning(
                    "mcp_config_server_invalid",
                    entry=entry,
                    error=str(exc),
                )
                continue

            self._configs[cfg.name] = cfg

            if cfg.enabled:
                await self.connect(cfg)
            else:
                logger.debug(
                    "mcp_server_skipped_disabled",
                    server=cfg.name,
                )

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self, server_config: MCPServerConfig) -> None:
        """Connect to a single MCP server and cache the connection.

        Creates an ``MCPServerConnection`` for the given config and calls
        ``connect()`` on it.  The connection is stored regardless of whether
        the underlying connect() call succeeded so that ``get_server_status()``
        can report on it.

        If a connection for ``server_config.name`` already exists it is
        disconnected before the new one is created.

        Args:
            server_config: Validated ``MCPServerConfig`` for the target server.
        """
        name = server_config.name

        # Gracefully tear down any pre-existing connection for this name.
        if name in self._connections:
            await self.disconnect(name)

        conn = MCPServerConnection(server_config)
        self._connections[name] = conn
        # connect() on MCPServerConnection never raises (AC-004-5); it logs
        # internally and marks the connection unavailable on failure.
        await conn.connect()
        logger.info(
            "mcp_manager_connect_done",
            server=name,
            connected=conn.connected,
        )

    async def disconnect(self, server_name: str) -> None:
        """Disconnect from a named MCP server and remove it from the registry.

        Safe to call even if ``server_name`` is not in the registry.

        Args:
            server_name: The name of the server as defined in config.
        """
        conn = self._connections.pop(server_name, None)
        if conn is None:
            logger.warning(
                "mcp_manager_disconnect_unknown",
                server=server_name,
            )
            return
        await conn.disconnect()
        logger.info("mcp_manager_disconnected", server=server_name)

    async def reload(self) -> None:
        """Hot-reload all server configs without losing still-valid connections.

        Algorithm:

        1. Re-read the config file to obtain the new set of server configs.
        2. For servers present in the new config and currently connected,
           keep the existing connection unchanged.
        3. For servers that have been removed from the config (or disabled),
           disconnect them.
        4. For newly added (or re-enabled) servers, call ``connect()``.

        If the config file is missing or invalid, a warning is logged and the
        existing connections are left untouched.
        """
        path = self._config_path

        if not path.exists():
            logger.warning("mcp_reload_config_not_found", path=str(path))
            return

        try:
            raw = path.read_text(encoding="utf-8")
            data = yaml.safe_load(raw)
        except (OSError, yaml.YAMLError) as exc:
            logger.error(
                "mcp_reload_config_error",
                path=str(path),
                error=str(exc),
            )
            return

        if not isinstance(data, dict):
            logger.warning("mcp_reload_unexpected_structure", path=str(path))
            return

        servers_raw: list[object] = data.get("servers") or []
        if not isinstance(servers_raw, list):
            return

        # Parse all valid, enabled configs from the new file.
        new_configs: dict[str, MCPServerConfig] = {}
        for entry in servers_raw:
            if not isinstance(entry, dict):
                continue
            try:
                cfg = MCPServerConfig.model_validate(entry)
            except Exception as exc:  # broad: pydantic ValidationError or other
                logger.warning(
                    "mcp_reload_invalid_entry",
                    entry=entry,
                    error=str(exc),
                )
                continue
            new_configs[cfg.name] = cfg

        # Determine servers to disconnect: currently connected but absent from
        # the new config, or present but now disabled.
        names_to_disconnect = [
            name
            for name in list(self._connections)
            if name not in new_configs or not new_configs[name].enabled
        ]
        for name in names_to_disconnect:
            await self.disconnect(name)
            # Also remove from tracked configs if removed entirely.
            self._configs.pop(name, None)

        # Connect new or re-enabled servers.
        for name, cfg in new_configs.items():
            self._configs[name] = cfg
            if cfg.enabled and name not in self._connections:
                await self.connect(cfg)

        logger.info(
            "mcp_reload_complete",
            active_servers=list(self._connections.keys()),
        )

    # ------------------------------------------------------------------
    # Tool access
    # ------------------------------------------------------------------

    def get_all_langchain_tools(self) -> list[BaseTool]:
        """Return all MCP tools from connected servers as LangChain BaseTool instances.

        Servers are ordered by their configured ``priority`` (highest first) so
        that the LLM sees preferred tools at the top of its tool list.  When
        multiple servers share the same priority tier the original config order
        is preserved as a stable tiebreaker.

        Tools from servers with ``priority > 0`` receive a description prefix
        that signals the preferred order to the LLM:

        * ``[Preferred]`` — highest configured priority among connected servers.
        * ``[Fallback]``  — lower non-zero priority; use only when the preferred
          tool is unavailable or has been exhausted.
        * *(no prefix)*  — priority 0; no ordering preference declared.

        The ``MCPToolAdapter`` import is deferred to call time so that this
        module does not hard-depend on T-052 at import time.  If the adapter
        module is not yet available an empty list is returned with a warning.

        Returns:
            A flat list of ``BaseTool`` instances, one per (server, tool) pair,
            sorted highest priority first.
        """
        try:
            # Lazy import — MCPToolAdapter is implemented in T-052.
            from lightagent.mcp.adapter import MCPToolAdapter
        except ImportError:
            logger.warning(
                "mcp_tool_adapter_unavailable",
                hint="MCPToolAdapter not yet implemented (T-052 pending)",
            )
            return []

        # Sort connected servers by priority descending; stable sort preserves
        # config-file order within the same priority tier.
        sorted_conns = sorted(
            (c for c in self._connections.values() if c.connected),
            key=lambda c: c._config.priority,
            reverse=True,
        )

        # Determine the maximum priority among connected servers with priority > 0
        # to distinguish "preferred" from "fallback" tiers.
        max_priority: int = max(
            (c._config.priority for c in sorted_conns if c._config.priority > 0),
            default=0,
        )

        tools: list[BaseTool] = []
        for conn in sorted_conns:
            p = conn._config.priority
            # Compute the description prefix for this server's priority tier.
            if p > 0 and p == max_priority:
                prefix = "[Preferred] "
            elif p > 0:
                prefix = "[Fallback — use only when Preferred tool is unavailable] "
            else:
                prefix = ""

            # cached_tools is populated during connect(); iterate directly to
            # avoid the async list_tools() call from a sync context.
            for mcp_tool in conn.cached_tools:
                adapter = MCPToolAdapter(conn, mcp_tool)
                if prefix:
                    adapter.description = f"{prefix}{adapter.description}"
                tools.append(adapter)

        if max_priority > 0:
            logger.debug(
                "mcp_tools_priority_sorted",
                max_priority=max_priority,
                servers=[
                    {"name": c._config.name, "priority": c._config.priority}
                    for c in sorted_conns
                    if c._config.priority > 0
                ],
            )

        return tools

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_server_status(self) -> list[MCPServerStatus]:
        """Return a status snapshot for every server that has been attempted.

        Only servers for which ``connect()`` was called are included.  Disabled
        servers that were never attempted (i.e. skipped by ``load_from_config``
        because ``enabled=False``) are **not** included in the returned list.

        Returns:
            List of ``MCPServerStatus`` instances, one per tracked server.
        """
        statuses: list[MCPServerStatus] = []
        for conn in self._connections.values():
            statuses.append(conn.status)
        return statuses

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Disconnect all servers and clear the registry.

        Should be called on application shutdown (e.g. FastAPI lifespan
        ``shutdown`` event) to cleanly tear down all MCP subprocess and
        HTTP connections.

        Each server is disconnected inside its own ``try/except`` so that a
        ``CancelledError`` or other ``BaseException`` raised by one server's
        transport teardown does not abort the cleanup of the remaining servers.
        """
        names = list(self._connections.keys())
        for name in names:
            try:
                await self.disconnect(name)
            except BaseException as exc:
                logger.warning(
                    "mcp_manager_close_error",
                    server=name,
                    error=str(exc),
                )
        logger.info("mcp_manager_closed")


__all__ = ["MCPClientManager"]
