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
from typing import Any, Literal

import psutil
from pydantic import BaseModel, Field, model_validator

import mcp.types
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from prismal.core.exceptions import MCPConnectionError, MCPToolError
from prismal.core.logging import get_logger


def resolve_secret(name: str) -> str:
    """Resolve a named secret for MCP auth (Phase W — SPEC-CSI-010).

    ``token_env`` on an MCP server config names *where* the bearer token lives.
    Resolution prefers the injected :class:`ConfigSourcePort` (so a host that owns
    configuration — Vault, AWS Secrets Manager, a tenant row — controls MCP auth
    too), and falls back to ``os.environ`` for parity when no source is injected
    or the name is absent from it.

    Args:
        name: The env-var / secret name (e.g. ``"SERPAPI_TOKEN"``).

    Returns:
        The resolved secret value, or ``""`` when unavailable. Never raises.
    """
    from pydantic import SecretStr

    from prismal.core.config_source import get_config_source

    source = get_config_source()
    if source is not None:
        try:
            value = source.load().get(name)
        except Exception:  # config source must never break MCP auth
            value = None
        if value is not None:
            return value.get_secret_value() if isinstance(value, SecretStr) else str(value)
    return os.environ.get(name, "")


def _kill_process_tree(pids: list[int]) -> None:
    """Terminate a list of PIDs and all their descendants.

    Sends SIGTERM first; after a 2-second wait force-kills survivors with
    SIGKILL.  Uses ``psutil`` for recursive child discovery.  No-ops silently
    if any PID has already exited.

    Args:
        pids: Top-level process IDs to terminate (e.g. the stdio subprocess).
    """
    if not pids:
        return
    try:
        procs: list[psutil.Process] = []
        for pid in pids:
            with contextlib.suppress(psutil.NoSuchProcess):
                proc = psutil.Process(pid)
                # Collect descendants before terminating the parent so they
                # cannot become orphans that escape the kill list.
                procs.extend(proc.children(recursive=True))
                procs.append(proc)

        for proc in procs:
            with contextlib.suppress(psutil.NoSuchProcess):
                proc.terminate()

        _, alive = psutil.wait_procs(procs, timeout=2)
        for proc in alive:
            with contextlib.suppress(psutil.NoSuchProcess):
                proc.kill()
    except Exception:  # noqa: S110 — best-effort cleanup; never raise
        pass


logger = get_logger("prismal.mcp.connection")

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
    type: Literal["stdio", "sse", "streamable-http"] = Field(
        ...,
        description=(
            "Transport type: 'stdio' (subprocess), 'sse' (HTTP SSE),"
            " or 'streamable-http' (MCP Streamable HTTP — modern remote servers)."
        ),
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
    priority: int = Field(
        default=0,
        ge=0,
        description=(
            "Dispatch priority for this server's tools (higher = preferred). "
            "When multiple servers offer similar functionality — e.g. two web-search "
            "providers — the agent sees higher-priority tools first in its tool list "
            "and their descriptions are annotated with '[Preferred]' or '[Fallback]' "
            "so the LLM knows which service to use first. "
            "Default 0 means no special preference over other priority-0 servers."
        ),
    )
    description: str | None = Field(
        default=None,
        description="Human-readable description of this server.",
    )
    capabilities: list[str] = Field(
        default_factory=lambda: ["general"],
        description=(
            "Tags consumed by Fase E capability-routing. A server is included "
            "in an agent's tool pool when its ``capabilities`` overlap with "
            "the ``required_capabilities`` passed to ``get_tools_for_agent`` "
            "(or when it has the ``general`` tag — which is universal). "
            "Omitting the field defaults to ``['general']`` so legacy configs "
            "remain visible to every agent."
        ),
    )

    @model_validator(mode="after")
    def _validate_transport_fields(self) -> MCPServerConfig:
        """Enforce that transport-specific fields are present.

        Raises:
            ValueError: If required transport fields are missing.
        """
        if self.type == "stdio" and not self.command:
            msg = f"MCP server '{self.name}': 'command' is required for stdio transport."
            raise ValueError(msg)
        if self.type in {"sse", "streamable-http"} and not self.url:
            msg = f"MCP server '{self.name}': 'url' is required for {self.type!r} transport."
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
    priority: int = Field(
        default=0,
        description=("Dispatch priority — higher value means tools from this server appear first."),
    )


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

        cfg = MCPServerConfig(name="myserver", type="stdio", command=["python", "-m", "myserver"])
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
        # PIDs of stdio subprocesses spawned by this connection (populated in
        # _open_stdio so they can be force-killed on disconnect/shutdown).
        self._subprocess_pids: list[int] = []

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        """Return ``True`` if the connection is currently active."""
        return self._connected

    @property
    def cached_tools(self) -> list[mcp.types.Tool]:
        """Return the cached tool list populated during connect()."""
        return self._cached_tools

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
            priority=self._config.priority,
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
            # Suppress any error from stack cleanup (e.g. ProcessLookupError
            # when the stdio subprocess already exited due to a timeout) so
            # that only the original MCPConnectionError is propagated.
            with contextlib.suppress(Exception):
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
        if self._config.type == "streamable-http":
            return await self._open_streamable_http(stack)
        # Defensive: Pydantic Literal prevents this at runtime
        msg = f"Unknown transport type: {self._config.type!r}"
        raise MCPConnectionError(server_name=self._config.name, reason=msg)

    async def _open_stdio(
        self,
        stack: contextlib.AsyncExitStack,
    ) -> tuple[Any, Any]:  # Any: anyio streams
        """Open a stdio (subprocess) transport.

        Snapshots the current child-process tree immediately before and after
        the transport is opened so that the new subprocess PIDs can be recorded
        in ``_subprocess_pids`` for force-kill on disconnect.

        Args:
            stack: The ``AsyncExitStack`` to register the context manager with.

        Returns:
            A ``(read, write)`` tuple.
        """
        cmd: list[str] = self._config.command or []  # validated non-empty by model
        # Expand ${VAR} / $VAR references in env values so that secrets stored
        # in the process environment (e.g. loaded from .env) are resolved at
        # connection time rather than stored as literals in the YAML config.
        raw_env = self._config.env or {}
        resolved_env: dict[str, str] | None = (
            {k: os.path.expandvars(v) for k, v in raw_env.items()} if raw_env else None
        )
        params = StdioServerParameters(
            command=cmd[0],
            args=cmd[1:],
            env=resolved_env,
        )

        # Snapshot existing child PIDs so we can identify the new subprocess.
        with contextlib.suppress(Exception):
            before_pids: set[int] = {p.pid for p in psutil.Process().children(recursive=True)}

        read, write = await stack.enter_async_context(stdio_client(params))

        # Record the new PIDs spawned by this transport for cleanup on shutdown.
        with contextlib.suppress(Exception):
            after_pids = {p.pid for p in psutil.Process().children(recursive=True)}
            self._subprocess_pids = list(after_pids - before_pids)

        return read, write

    async def _open_sse(
        self,
        stack: contextlib.AsyncExitStack,
    ) -> tuple[Any, Any]:  # Any: anyio streams
        """Open an SSE (HTTP) transport with optional bearer auth (AC-004-9).

        Note:
            ``sse_read_timeout`` is not currently set on the ``sse_client``
            call, so long-running SSE streams are bounded only by the
            ``asyncio.wait_for`` timeout applied at the tool-call level.
            This is a known architectural limitation.

        Args:
            stack: The ``AsyncExitStack`` to register the context manager with.

        Returns:
            A ``(read, write)`` tuple.
        """
        # Expand ${VAR} / $VAR references so secrets can be stored in env, not YAML.
        url: str = os.path.expandvars(self._config.url or "")
        headers: dict[str, str] = {}
        if self._config.auth and self._config.auth.type == "bearer":
            token = resolve_secret(self._config.auth.token_env)
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

    async def _open_streamable_http(
        self,
        stack: contextlib.AsyncExitStack,
    ) -> tuple[Any, Any]:  # Any: anyio streams
        """Open a Streamable HTTP transport (MCP 2025 spec).

        Modern remote MCP servers (e.g. SerpAPI, Claude Desktop remote MCPs)
        use this transport instead of SSE.  It supports the same optional
        bearer-token auth as the SSE transport.

        Args:
            stack: The ``AsyncExitStack`` to register the context manager with.

        Returns:
            A ``(read, write)`` tuple.
        """
        from mcp.client.streamable_http import streamablehttp_client

        url: str = os.path.expandvars(self._config.url or "")
        headers: dict[str, str] = {}
        if self._config.auth and self._config.auth.type == "bearer":
            token = resolve_secret(self._config.auth.token_env)
            if not token:
                logger.warning(
                    "mcp_streamable_http_auth_token_missing",
                    server=self._config.name,
                    token_env=self._config.auth.token_env,
                )
            headers["Authorization"] = f"Bearer {token}"
        read, write, _ = await stack.enter_async_context(
            streamablehttp_client(
                url=url,
                headers=headers,
                timeout=float(self._config.timeout_seconds),
            )
        )
        return read, write

    async def _cleanup_stack(self) -> None:
        """Close and discard the current AsyncExitStack if present.

        Closes the stack first (with a 5-second timeout) so that anyio's
        ``create_task_group()`` inside ``stdio_client`` can exit cleanly from
        the same asyncio task that entered it.  Killing the subprocess BEFORE
        ``aclose()`` would break the stdin/stdout pipes while background tasks
        are still running, causing anyio to raise ``RuntimeError: Attempted to
        exit cancel scope in a different task than it was entered in``.

        After ``aclose()`` completes (or times out), ``_kill_process_tree`` is
        called as a safety net to terminate any surviving processes.  Because
        ``_open_stdio`` records ALL new PIDs recursively (including ``node``
        grandchildren of ``npm``), these can still be killed by PID even after
        they have been reparented to init.

        Catches ``BaseException`` (which includes ``asyncio.CancelledError``)
        so that a SIGINT/SIGTERM-triggered cancellation during shutdown does
        not propagate out.  Cleanup of state variables proceeds in the
        ``finally`` block regardless.
        """
        if self._exit_stack is not None:
            pids = self._subprocess_pids
            self._subprocess_pids = []

            try:
                # aclose() first — lets anyio task group exit from the correct
                # task and cancels background stdin/stdout tasks gracefully.
                await asyncio.wait_for(self._exit_stack.aclose(), timeout=5.0)
            except BaseException as exc:
                logger.warning(
                    "mcp_stack_cleanup_error",
                    server=self._config.name,
                    error=str(exc),
                )
            finally:
                # Kill any surviving processes after the stack is closed.
                _kill_process_tree(pids)
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
