"""MCP server connection management.

Implements ``MCPServerConnection`` — a class that manages a single MCP server
connection supporting both ``stdio`` and ``sse`` transport types.  Each
connection keeps its context managers alive via ``contextlib.AsyncExitStack``
so that the underlying read/write streams remain open for the lifetime of the
``MCPServerConnection`` object.

References:
    - SPEC-004 (AC-004-5, AC-004-6, AC-004-8, AC-004-9)
    - MCP Python SDK ≥ 1.2.0
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from datetime import timedelta
from typing import Any, Literal

import mcp.types
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from pydantic import BaseModel, Field, model_validator

from lightagent.core.exceptions import MCPConnectionError, MCPToolError
from lightagent.core.logging import get_logger
from mcp import ClientSession

logger = get_logger("lightagent.mcp.connection")

# ---------------------------------------------------------------------------
# Pydantic config / status models
# ---------------------------------------------------------------------------


class MCPAuthConfig(BaseModel):
    """Authentication configuration for an MCP server.

    Currently supports bearer-token authentication only.  The actual token
    value is read from an environment variable named by ``token_env`` at
    connection time so that secrets are never stored in config files.
    """

    type: Literal["bearer"] = Field(
        default="bearer",
        description="Auth scheme — only 'bearer' is supported.",
    )
    token_env: str = Field(
        ...,
        description="Name of the environment variable that holds the bearer token.",
    )


class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server entry.

    Matches the YAML schema defined in ``config/mcp_servers.yaml``.

    Example::

        MCPServerConfig(
            name="myserver",
            type="stdio",
            enabled=True,
            command=["python", "-m", "myserver"],
            timeout_seconds=30,
        )
    """

    name: str = Field(..., description="Unique identifier for this MCP server.")
    type: Literal["stdio", "sse"] = Field(
        ...,
        description="Transport type: 'stdio' (subprocess) or 'sse' (HTTP SSE).",
    )
    enabled: bool = Field(
        default=True,
        description="Whether this server is loaded on startup.",
    )
    # stdio fields
    command: list[str] | None = Field(
        default=None,
        description="Command to launch the server process (stdio transport only).",
    )
    env: dict[str, str] | None = Field(
        default=None,
        description="Extra environment variables passed to the server process (stdio).",
    )
    # sse fields
    url: str | None = Field(
        default=None,
        description="SSE endpoint URL (sse transport only).",
    )
    auth: MCPAuthConfig | None = Field(
        default=None,
        description="Optional bearer-token auth config (sse transport only).",
    )
    # common
    timeout_seconds: int = Field(
        default=30,
        ge=1,
        description="Per-request timeout in seconds.",
    )
    retry_attempts: int = Field(
        default=3,
        ge=0,
        description="Number of retry attempts on transient connection failures.",
    )
    description: str | None = Field(
        default=None,
        description="Human-readable description of this server.",
    )

    @model_validator(mode="after")
    def _validate_transport_fields(self) -> MCPServerConfig:
        """Enforce that transport-specific fields are present.

        Raises:
            ValueError: If required transport fields are missing.
        """
        if self.type == "stdio" and not self.command:
            msg = (
                f"MCP server '{self.name}': "
                "'command' is required for stdio transport."
            )
            raise ValueError(msg)
        if self.type == "sse" and not self.url:
            msg = f"MCP server '{self.name}': 'url' is required for sse transport."
            raise ValueError(msg)
        return self


class MCPServerStatus(BaseModel):
    """Current connection status snapshot for an MCP server."""

    name: str = Field(..., description="Server name from config.")
    connected: bool = Field(..., description="Whether the connection is active.")
    tool_count: int = Field(
        default=0,
        description="Number of tools exposed by this server.",
    )
    error: str | None = Field(
        default=None,
        description="Last error message if the server is unavailable.",
    )
    server_type: str = Field(..., description="Transport type ('stdio' or 'sse').")


# ---------------------------------------------------------------------------
# MCPServerConnection
# ---------------------------------------------------------------------------


class MCPServerConnection:
    """Manages a persistent connection to a single MCP server.

    Supports both ``stdio`` (subprocess) and ``sse`` (HTTP Server-Sent Events)
    transport types.  The connection is kept alive using an
    ``contextlib.AsyncExitStack`` so that the underlying transport context
    managers remain open for the lifetime of this object.

    Connection errors are logged and the server is marked as unavailable
    without propagating exceptions to the caller (AC-004-5).  Per-server
    timeouts are enforced for every tool call (AC-004-6).

    Usage::

        cfg = MCPServerConfig(name="myserver", type="stdio",
                              command=["python", "-m", "myserver"])
        conn = MCPServerConnection(cfg)
        await conn.connect()
        tools = await conn.list_tools()
        result = await conn.call_tool("my_tool", {"arg": "value"})
        await conn.disconnect()
    """

    def __init__(self, config: MCPServerConfig) -> None:
        """Initialise the connection with the given server config.

        Args:
            config: Validated ``MCPServerConfig`` for this server.
        """
        self._config = config
        self._session: ClientSession | None = None
        self._exit_stack: contextlib.AsyncExitStack | None = None
        self._connected: bool = False
        self._last_error: str | None = None
        self._cached_tools: list[mcp.types.Tool] = []

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        """Return ``True`` if the connection is currently active."""
        return self._connected

    @property
    def status(self) -> MCPServerStatus:
        """Return a snapshot of the current connection status.

        Returns:
            ``MCPServerStatus`` reflecting the current state.
        """
        return MCPServerStatus(
            name=self._config.name,
            connected=self._connected,
            tool_count=len(self._cached_tools),
            error=self._last_error,
            server_type=self._config.type,
        )

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Connect to the MCP server and cache its tool list.

        Attempts to establish the transport-level connection and initialise the
        ``ClientSession``.  On success the cached tool list is populated and
        ``connected`` is set to ``True``.

        On failure the exception is caught, the error is logged, and
        ``connected`` is set to ``False`` — the method never raises to the
        caller (AC-004-5).

        Retries up to ``config.retry_attempts`` times on transient errors.
        """
        attempt = 0
        max_attempts = max(1, self._config.retry_attempts)
        while attempt < max_attempts:
            attempt += 1
            try:
                await self._do_connect()
                return
            except Exception as exc:
                self._last_error = str(exc)
                if attempt < max_attempts:
                    logger.warning(
                        "mcp_connect_retry",
                        server=self._config.name,
                        attempt=attempt,
                        max_attempts=max_attempts,
                        error=self._last_error,
                    )
                else:
                    logger.error(
                        "mcp_connect_failed",
                        server=self._config.name,
                        attempts=attempt,
                        error=self._last_error,
                    )
                    self._connected = False

    async def _do_connect(self) -> None:
        """Internal: open transport, create session, initialise, cache tools.

        Raises:
            MCPConnectionError: If the transport or session setup fails.
        """
        # Close any previous connection cleanly before re-opening.
        await self._cleanup_stack()

        stack = contextlib.AsyncExitStack()
        try:
            read, write = await self._open_transport(stack)
            session = await stack.enter_async_context(ClientSession(read, write))
            await asyncio.wait_for(
                session.initialize(),
                timeout=self._config.timeout_seconds,
            )
            self._session = session
            self._exit_stack = stack
            self._connected = True
            self._last_error = None
            logger.info(
                "mcp_connected",
                server=self._config.name,
                transport=self._config.type,
            )
            # Pre-populate the tool cache so callers can immediately introspect.
            await self._refresh_tools()
        except Exception as exc:
            await stack.aclose()
            raise MCPConnectionError(
                server_name=self._config.name,
                reason=str(exc),
            ) from exc

    async def _open_transport(
        self,
        stack: contextlib.AsyncExitStack,
    ) -> tuple[Any, Any]:  # Any: anyio MemoryObject streams — no public type alias
        """Open the configured transport and return (read, write) streams.

        Args:
            stack: The ``AsyncExitStack`` used to keep the transport alive.

        Returns:
            A ``(read, write)`` tuple of anyio memory-object streams.

        Raises:
            MCPConnectionError: If the transport type is invalid or setup fails.
        """
        if self._config.type == "stdio":
            return await self._open_stdio(stack)
        if self._config.type == "sse":
            return await self._open_sse(stack)
        msg = f"Unknown transport type: {self._config.type!r}"
        raise MCPConnectionError(server_name=self._config.name, reason=msg)

    async def _open_stdio(
        self,
        stack: contextlib.AsyncExitStack,
    ) -> tuple[Any, Any]:  # Any: anyio streams
        """Open a stdio (subprocess) transport.

        Args:
            stack: The ``AsyncExitStack`` to register the context manager with.

        Returns:
            A ``(read, write)`` tuple.
        """
        cmd: list[str] = self._config.command or []  # validated non-empty by model
        params = StdioServerParameters(
            command=cmd[0],
            args=cmd[1:],
            env=self._config.env or None,
        )
        read, write = await stack.enter_async_context(stdio_client(params))
        return read, write

    async def _open_sse(
        self,
        stack: contextlib.AsyncExitStack,
    ) -> tuple[Any, Any]:  # Any: anyio streams
        """Open an SSE (HTTP) transport with optional bearer auth (AC-004-9).

        Args:
            stack: The ``AsyncExitStack`` to register the context manager with.

        Returns:
            A ``(read, write)`` tuple.
        """
        url: str = self._config.url or ""  # validated non-empty by model
        headers: dict[str, str] = {}
        if self._config.auth and self._config.auth.type == "bearer":
            token = os.environ.get(self._config.auth.token_env, "")
            if not token:
                logger.warning(
                    "mcp_sse_auth_token_missing",
                    server=self._config.name,
                    token_env=self._config.auth.token_env,
                )
            headers["Authorization"] = f"Bearer {token}"
        read, write = await stack.enter_async_context(
            sse_client(
                url=url,
                headers=headers,
                timeout=self._config.timeout_seconds,
            )
        )
        return read, write

    async def _cleanup_stack(self) -> None:
        """Close and discard the current AsyncExitStack if present."""
        if self._exit_stack is not None:
            try:
                await self._exit_stack.aclose()
            except Exception as exc:
                logger.warning(
                    "mcp_stack_cleanup_error",
                    server=self._config.name,
                    error=str(exc),
                )
            finally:
                self._exit_stack = None
                self._session = None
                self._connected = False

    async def disconnect(self) -> None:
        """Gracefully disconnect from the MCP server.

        Closes the ``AsyncExitStack`` which tears down both the ``ClientSession``
        and the underlying transport.  Safe to call even if not connected.
        """
        await self._cleanup_stack()
        self._cached_tools = []
        logger.info("mcp_disconnected", server=self._config.name)

    # ------------------------------------------------------------------
    # Tool operations
    # ------------------------------------------------------------------

    async def list_tools(self) -> list[mcp.types.Tool]:
        """Return the list of tools exposed by this server.

        Returns the cached list if available; otherwise fetches from the server.

        Returns:
            List of ``mcp.types.Tool`` objects.

        Raises:
            MCPConnectionError: If the server is not connected.
        """
        if not self._connected or self._session is None:
            raise MCPConnectionError(
                server_name=self._config.name,
                reason="Not connected — call connect() first.",
            )
        if not self._cached_tools:
            await self._refresh_tools()
        return list(self._cached_tools)

    async def _refresh_tools(self) -> None:
        """Fetch the tool list from the server and update the cache.

        Raises:
            MCPConnectionError: If the session is missing or the request times out.
        """
        if self._session is None:
            raise MCPConnectionError(
                server_name=self._config.name,
                reason="Session is not initialised.",
            )
        try:
            result = await asyncio.wait_for(
                self._session.list_tools(),
                timeout=self._config.timeout_seconds,
            )
            self._cached_tools = list(result.tools)
            logger.debug(
                "mcp_tools_cached",
                server=self._config.name,
                count=len(self._cached_tools),
            )
        except TimeoutError as exc:
            raise MCPConnectionError(
                server_name=self._config.name,
                reason=f"list_tools timed out after {self._config.timeout_seconds}s",
            ) from exc

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],  # Any: MCP JSON-compatible values — unavoidable
    ) -> mcp.types.CallToolResult:
        """Call a tool on the MCP server with timeout enforcement (AC-004-6).

        Args:
            name: The tool name as registered on the server.
            arguments: JSON-compatible keyword arguments for the tool.

        Returns:
            ``mcp.types.CallToolResult`` from the server.

        Raises:
            MCPConnectionError: If the server is not connected.
            MCPToolError: If the tool call times out or raises an exception.
        """
        if not self._connected or self._session is None:
            raise MCPConnectionError(
                server_name=self._config.name,
                reason="Not connected — call connect() first.",
            )
        try:
            result = await asyncio.wait_for(
                self._session.call_tool(
                    name,
                    arguments=arguments,
                    read_timeout_seconds=timedelta(
                        seconds=self._config.timeout_seconds
                    ),
                ),
                timeout=self._config.timeout_seconds,
            )
            logger.debug(
                "mcp_tool_called",
                server=self._config.name,
                tool=name,
                is_error=result.isError,
            )
            return result
        except TimeoutError as exc:
            raise MCPToolError(
                tool_name=name,
                server_name=self._config.name,
                reason=f"timed out after {self._config.timeout_seconds}s",
            ) from exc
        except MCPConnectionError:
            raise
        except Exception as exc:
            raise MCPToolError(
                tool_name=name,
                server_name=self._config.name,
                reason=str(exc),
            ) from exc


__all__ = [
    "MCPAuthConfig",
    "MCPServerConfig",
    "MCPServerConnection",
    "MCPServerStatus",
]
