"""MCP protocol adapters — legacy session vs. 2026-07-28 stateless (SPEC-PRT-MCP-001).

Two adapters behind one :class:`McpProtocolAdapter` port:

* :class:`LegacySessionAdapter` wraps today's :class:`~prismal.mcp.connection.MCPServerConnection`
  (``mcp`` SDK 1.28.1 ``ClientSession``: ``initialize`` → ``tools/list`` → ``tools/call``)
  byte-for-byte — it only *delegates* (ADR-PRT-001 §2.2: no SDK bump).
* :class:`StatelessHttpAdapter` speaks the 2026-07-28 stateless wire in-house on ``httpx``:
  no ``initialize``/session, ``_meta`` + ``MCP-Protocol-Version``/``Mcp-Method``/``Mcp-Name`` on
  every request, ``server/discover``, ``tools/list`` pagination, multi-round-trip input requests
  and the tasks extension (only when declared — fail closed).

:func:`select_adapter` picks one per ``settings.mcp_protocol_mode`` (``legacy`` default ⇒ today).
"""

from __future__ import annotations

import contextlib
import itertools
import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from prismal.core.exceptions import McpDiscoveryError, McpProtocolError, McpTaskError
from prismal.core.logging import get_logger
from prismal.mcp import constants as c

if TYPE_CHECKING:
    import httpx

    from prismal.core.config import Settings
    from prismal.mcp.connection import MCPServerConfig, MCPServerConnection
    from prismal.mcp.input_requests import InputRequestHandler

logger = get_logger("prismal.mcp.protocol")

McpProtocolMode = Literal["auto", "legacy", "stateless"]
#: Scope of a cached tool list: ``shared`` (MCP ``public``) or ``principal`` (MCP ``private``).
CacheScope = Literal["shared", "principal"]
#: Upper bound for server-supplied millisecond hints (``ttlMs`` / ``pollIntervalMs``): ~1 year.
#: An untrusted server may send any JSON integer; unbounded values overflow float math downstream.
MAX_HINT_MS = 365 * 24 * 3600 * 1000


class MetricsSink(Protocol):
    """The slice of :class:`~prismal.monitoring.otel.OTelManager` used here."""

    def increment_counter(
        self, metric: str, value: int = 1, attributes: dict[str, Any] | None = None
    ) -> None: ...


def default_metrics() -> MetricsSink:
    """Return the process ``OTelManager`` (no-op when OTel is disabled)."""
    from prismal.monitoring.otel import OTelManager

    return OTelManager()


# ── Value objects ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ServerDiscovery:
    """What a server told us about itself (``server/discover`` or legacy ``initialize``)."""

    protocol_version: str
    stateless: bool
    capabilities: Mapping[str, Any] = field(default_factory=dict)
    extensions: frozenset[str] = frozenset()
    server_name: str = ""
    server_version: str = ""
    supported_versions: tuple[str, ...] = ()
    instructions: str | None = None


@dataclass(frozen=True)
class ToolListPage:
    """All pages of a ``tools/list`` with the cache hints (``None`` = server sent none)."""

    tools: tuple[Mapping[str, Any], ...]
    ttl_ms: int | None = None
    cache_scope: CacheScope | None = None


@dataclass(frozen=True)
class McpTaskHandle:
    """A task created by a task-augmented ``tools/call`` (tasks extension)."""

    server: str
    task_id: str
    created_at: float
    poll_hint_ms: int | None = None
    status: str = c.TASK_STATUS_WORKING


@dataclass(frozen=True)
class McpTaskStatus:
    """One ``tasks/get`` snapshot."""

    task_id: str
    status: str
    result: Mapping[str, Any] | None = None
    status_message: str | None = None
    poll_interval_ms: int | None = None
    ttl_ms: int | None = None

    @property
    def terminal(self) -> bool:
        """``True`` once the task reached ``completed`` / ``failed`` / ``cancelled``."""
        return self.status in c.TERMINAL_TASK_STATUSES


@dataclass(frozen=True)
class ToolCallOutcome:
    """Either an immediate ``tools/call`` result or a task handle to poll."""

    result: Mapping[str, Any] | None = None
    task: McpTaskHandle | None = None
    is_error: bool = False


@runtime_checkable
class McpProtocolAdapter(Protocol):
    """One MCP server, one protocol revision (SPEC-PRT-MCP-001)."""

    @property
    def discovery(self) -> ServerDiscovery: ...

    async def list_tools(self) -> ToolListPage: ...

    async def call_tool(
        self, name: str, arguments: Mapping[str, Any], *, allow_task: bool = False
    ) -> ToolCallOutcome: ...

    async def get_task(self, task_id: str) -> McpTaskStatus: ...

    async def cancel_task(self, task_id: str) -> None: ...

    async def aclose(self) -> None: ...


# ── Errors carrying wire detail (subclasses of the shared McpProtocolError) ──


class McpJsonRpcError(McpProtocolError):
    """A JSON-RPC ``error`` object returned by a stateless server."""

    def __init__(self, code: int, message: str, *, http_status: int | None = None) -> None:
        self.code = code
        self.http_status = http_status
        super().__init__(f"MCP JSON-RPC error {code}: {message}")


class McpHttpError(McpProtocolError):
    """A non-2xx HTTP answer without a JSON-RPC error body."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"MCP HTTP error {status_code}")


# ── Legacy adapter (today's SDK session path, delegated byte-for-byte) ───────


def _dump(model: Any) -> dict[str, Any]:
    dumped: dict[str, Any] = model.model_dump(mode="json", by_alias=True, exclude_none=True)
    return dumped


class LegacySessionAdapter:
    """Wrap an :class:`MCPServerConnection` (``initialize`` + ``ClientSession``; SDK 1.28.1).

    Every call delegates to the connection, so the request sequence is exactly today's. The SDK
    negotiates the version inside ``initialize``; the connection does not retain it, so the
    discovery reports the SDK's latest legacy revision. The tasks extension is not available here.
    """

    def __init__(self, connection: MCPServerConnection) -> None:
        self._connection = connection
        config = connection._config  # sibling module: the connection owns its config
        self._discovery = ServerDiscovery(
            protocol_version=c.LEGACY_PROTOCOL_VERSION,
            stateless=False,
            server_name=config.name,
        )

    @property
    def connection(self) -> MCPServerConnection:
        """The wrapped connection (used by the LangChain adapters)."""
        return self._connection

    @property
    def discovery(self) -> ServerDiscovery:
        """Static legacy discovery (no ``server/discover`` on this path)."""
        return self._discovery

    async def connect(self) -> None:
        """Today's ``MCPServerConnection.connect()`` (never raises)."""
        await self._connection.connect()

    async def list_tools(self) -> ToolListPage:
        """``tools/list`` via the session; legacy lists carry no cache hints."""
        tools = await self._connection.list_tools()
        return ToolListPage(tools=tuple(_dump(t) for t in tools))

    async def call_tool(
        self, name: str, arguments: Mapping[str, Any], *, allow_task: bool = False
    ) -> ToolCallOutcome:
        """``tools/call`` via the session (``allow_task`` is ignored: no tasks extension)."""
        del allow_task
        result = await self._connection.call_tool(name, dict(arguments))
        return ToolCallOutcome(result=_dump(result), is_error=bool(result.isError))

    async def get_task(self, task_id: str) -> McpTaskStatus:
        """Not supported on the legacy path."""
        raise McpTaskError(f"task {task_id!r}: tasks extension unavailable on the legacy adapter")

    async def cancel_task(self, task_id: str) -> None:
        """Not supported on the legacy path."""
        raise McpTaskError(f"task {task_id!r}: tasks extension unavailable on the legacy adapter")

    async def aclose(self) -> None:
        """Disconnect the wrapped connection."""
        await self._connection.disconnect()


# ── Stateless adapter (2026-07-28, in-house on httpx) ────────────────────────


def _hint_ms(value: Any) -> int | None:
    """A server-supplied ms hint as an int in ``[0, MAX_HINT_MS]``; non-integers ⇒ ``None``."""
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    return min(max(value, 0), MAX_HINT_MS)


def _error_code(value: Any) -> int:
    """A JSON-RPC ``error.code``; anything not integral (``"x"``, ``null``, NaN, ∞) ⇒ ``0``."""
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return 0


def _map_cache_scope(scopes: list[Any]) -> CacheScope | None:
    """Combine per-page ``cacheScope`` values; any missing ⇒ ``None`` (fail closed later)."""
    if not scopes or any(s not in c.CACHE_SCOPE_MAP for s in scopes):
        return None
    return "principal" if c.CACHE_SCOPE_PRIVATE in scopes else "shared"


class StatelessHttpAdapter:
    """2026-07-28 stateless MCP over HTTP: no ``initialize``, ``_meta`` + headers per request."""

    def __init__(
        self,
        server: MCPServerConfig,
        *,
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        input_handler: InputRequestHandler | None = None,
        org_id: str | None = None,
        metrics: MetricsSink | None = None,
        discovery: ServerDiscovery | None = None,
        max_input_rounds: int = 3,
        max_pages: int = 100,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        import httpx

        if settings is None:
            from prismal.core.config import get_settings

            settings = get_settings()
        self._server = server
        self._url = os.path.expandvars(server.url or "")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            transport=transport, timeout=float(server.timeout_seconds)
        )
        self._version = settings.mcp_stateless_protocol_version
        self._tasks_enabled = bool(settings.mcp_tasks_enabled)
        self._input_handler = input_handler
        self._org_id = org_id
        self._metrics = metrics if metrics is not None else default_metrics()
        self._discovery = discovery
        self._max_input_rounds = max_input_rounds
        self._max_pages = max_pages
        self._clock = clock
        self._ids = itertools.count(1)

    # -- wire helpers ---------------------------------------------------------

    def _client_capabilities(self) -> dict[str, Any]:
        caps: dict[str, Any] = {"elicitation": {"form": {}, "url": {}}}
        if self._tasks_enabled:
            caps["extensions"] = {c.TASKS_EXTENSION_ID: {}}
        return caps

    def _headers(self, method: str, name: str | None) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            c.HEADER_PROTOCOL_VERSION: self._version,
            c.HEADER_METHOD: method,
        }
        if name is not None:
            headers[c.HEADER_NAME] = name
        auth = self._server.auth
        if auth is not None and auth.type == "bearer":
            from prismal.mcp.connection import resolve_secret

            headers["Authorization"] = f"Bearer {resolve_secret(auth.token_env)}"
        return headers

    async def _rpc(
        self, method: str, params: Mapping[str, Any], *, name: str | None = None
    ) -> dict[str, Any]:
        """One JSON-RPC request → its ``result`` (errors mapped to :class:`McpProtocolError`)."""
        import httpx

        req_id = next(self._ids)
        meta = dict(params.get("_meta") or {})
        meta.update(
            {
                c.META_PROTOCOL_VERSION: self._version,
                c.META_CLIENT_CAPABILITIES: self._client_capabilities(),
                c.META_CLIENT_INFO: {"name": "prismal", "version": _client_version()},
            }
        )
        body = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": {**params, "_meta": meta},
        }
        try:
            response = await self._client.post(
                self._url, json=body, headers=self._headers(method, name)
            )
        except httpx.HTTPError as exc:
            raise McpProtocolError(f"MCP transport error on {method}: {exc}") from exc
        message = _decode_response(response, req_id)
        error = message.get("error") if message else None
        if isinstance(error, Mapping):
            raise McpJsonRpcError(
                _error_code(error.get("code", 0)),
                str(error.get("message", "")),
                http_status=response.status_code,
            )
        if response.status_code >= 400:
            raise McpHttpError(response.status_code)
        if message is None or not isinstance(message.get("result"), Mapping):
            raise McpProtocolError(f"MCP {method}: malformed response")
        return dict(message["result"])

    async def _request(
        self, method: str, params: Mapping[str, Any], *, name: str | None = None
    ) -> dict[str, Any]:
        """:meth:`_rpc` + the multi-round-trip loop (bounded by ``max_input_rounds``)."""
        from prismal.mcp.input_requests import answer_input_requests

        current: dict[str, Any] = dict(params)
        for _ in range(self._max_input_rounds + 1):
            result = await self._rpc(method, current, name=name)
            if result.get("resultType") != c.RESULT_TYPE_INPUT_REQUIRED:
                return result
            responses = await answer_input_requests(
                result.get("inputRequests") or {},
                handler=self._input_handler,
                server=self._server.name,
                org_id=self._org_id,
                stateless=True,
                metrics=self._metrics,
            )
            current = {**params, "inputResponses": responses}
            if "requestState" in result:
                current["requestState"] = result["requestState"]
        msg = f"MCP {method}: input still required after {self._max_input_rounds} rounds"
        raise McpProtocolError(msg)

    # -- port -----------------------------------------------------------------

    async def discover(self) -> ServerDiscovery:
        """``server/discover`` → :class:`ServerDiscovery` (stateless iff our version is listed)."""
        result = await self._rpc(c.METHOD_SERVER_DISCOVER, {})
        versions = result.get("supportedVersions")
        supported = tuple(str(v) for v in versions) if isinstance(versions, list) else ()
        stateless = self._version in supported
        caps = result.get("capabilities") or {}
        extensions = caps.get("extensions") if isinstance(caps, Mapping) else None
        info = result.get("serverInfo")
        if not isinstance(info, Mapping):
            info = {}
        self._discovery = ServerDiscovery(
            protocol_version=self._version if stateless else (supported[0] if supported else ""),
            stateless=stateless,
            capabilities=dict(caps) if isinstance(caps, Mapping) else {},
            extensions=frozenset(extensions) if isinstance(extensions, Mapping) else frozenset(),
            server_name=str(info.get("name", "")),
            server_version=str(info.get("version", "")),
            supported_versions=supported,
            instructions=result.get("instructions"),
        )
        return self._discovery

    @property
    def discovery(self) -> ServerDiscovery:
        """The last discovery; raises :class:`McpDiscoveryError` before :meth:`discover`."""
        if self._discovery is None:
            raise McpDiscoveryError(f"MCP server {self._server.name!r}: not discovered yet")
        return self._discovery

    async def list_tools(self) -> ToolListPage:
        """All ``tools/list`` pages (``nextCursor``), with combined ``ttlMs`` / ``cacheScope``."""
        tools: list[Mapping[str, Any]] = []
        ttls: list[int] = []
        scopes: list[Any] = []
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(self._max_pages):
            params: dict[str, Any] = {"cursor": cursor} if cursor else {}
            result = await self._request(c.METHOD_TOOLS_LIST, params)
            page_tools = result.get("tools") or []
            if not isinstance(page_tools, list):
                raise McpProtocolError("MCP tools/list: 'tools' is not a list")
            tools.extend(t for t in page_tools if isinstance(t, Mapping))
            ttl = _hint_ms(result.get("ttlMs"))
            if ttl is not None:
                ttls.append(ttl)
            scopes.append(result.get("cacheScope"))
            cursor = result.get("nextCursor") or None
            if cursor is not None and not isinstance(cursor, str):
                raise McpProtocolError("MCP tools/list: 'nextCursor' is not a string")
            if cursor is None:
                return ToolListPage(
                    tools=tuple(tools),
                    ttl_ms=min(ttls) if ttls else None,
                    cache_scope=_map_cache_scope(scopes),
                )
            if cursor in seen:
                raise McpProtocolError(f"MCP tools/list: repeated cursor {cursor!r}")
            seen.add(cursor)
        raise McpProtocolError(f"MCP tools/list: more than {self._max_pages} cursor pages")

    async def call_tool(
        self, name: str, arguments: Mapping[str, Any], *, allow_task: bool = False
    ) -> ToolCallOutcome:
        """``tools/call``; a task result is accepted only if declared + allowed (fail closed)."""
        result = await self._request(
            c.METHOD_TOOLS_CALL, {"name": name, "arguments": dict(arguments)}, name=name
        )
        if result.get("resultType") == c.RESULT_TYPE_TASK:
            task_id = str(result.get("taskId", ""))
            if not (allow_task and self._tasks_enabled):
                with contextlib.suppress(Exception):  # best-effort: don't leave it running
                    await self._rpc(c.METHOD_TASKS_CANCEL, {"taskId": task_id})
                msg = f"tool {name!r} returned task {task_id!r} but tasks were not accepted"
                raise McpTaskError(msg)
            return ToolCallOutcome(
                task=McpTaskHandle(
                    server=self._server.name,
                    task_id=task_id,
                    created_at=self._clock(),
                    poll_hint_ms=_hint_ms(result.get("pollIntervalMs")),
                    status=str(result.get("status", c.TASK_STATUS_WORKING)),
                )
            )
        return ToolCallOutcome(result=result, is_error=bool(result.get("isError")))

    def _require_tasks(self, task_id: str) -> None:
        if not self._tasks_enabled:
            raise McpTaskError(f"task {task_id!r}: tasks extension not declared")

    async def get_task(self, task_id: str) -> McpTaskStatus:
        """``tasks/get``; an ``input_required`` task is answered through ``tasks/update``."""
        from prismal.mcp.input_requests import answer_input_requests

        self._require_tasks(task_id)
        result = await self._rpc(c.METHOD_TASKS_GET, {"taskId": task_id})
        status = str(result.get("status", ""))
        requests = result.get("inputRequests")
        if status == c.TASK_STATUS_INPUT_REQUIRED and isinstance(requests, Mapping) and requests:
            responses = await answer_input_requests(
                requests,
                handler=self._input_handler,
                server=self._server.name,
                org_id=self._org_id,
                stateless=True,
                metrics=self._metrics,
            )
            await self._rpc(c.METHOD_TASKS_UPDATE, {"taskId": task_id, "inputResponses": responses})
        task_result = result.get("result")
        return McpTaskStatus(
            task_id=task_id,
            status=status,
            result=dict(task_result) if isinstance(task_result, Mapping) else None,
            status_message=result.get("statusMessage"),
            poll_interval_ms=_hint_ms(result.get("pollIntervalMs")),
            ttl_ms=_hint_ms(result.get("ttlMs")),
        )

    async def cancel_task(self, task_id: str) -> None:
        """``tasks/cancel``."""
        self._require_tasks(task_id)
        await self._rpc(c.METHOD_TASKS_CANCEL, {"taskId": task_id})

    async def aclose(self) -> None:
        """Close the HTTP client when this adapter created it."""
        if self._owns_client:
            await self._client.aclose()


def _client_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("prismal")
    except PackageNotFoundError:  # pragma: no cover - editable installs always resolve
        return "0"


def _decode_response(response: httpx.Response, req_id: int) -> dict[str, Any] | None:
    """Parse a JSON or SSE body into the JSON-RPC message answering *req_id*."""
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in response.text.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                message = json.loads(line[5:].strip())
            except ValueError:
                continue
            if isinstance(message, dict) and message.get("id") == req_id:
                return message
        return None
    try:
        message = response.json()
    except ValueError:
        if response.status_code >= 400:
            return None
        raise McpProtocolError("MCP response is not JSON") from None
    return message if isinstance(message, dict) else None


# ── Selection ────────────────────────────────────────────────────────────────


@dataclass
class _MemoEntry:
    mode: Literal["legacy", "stateless"]
    discovery: ServerDiscovery | None
    expires_at: float


class ProtocolModeMemo:
    """Per-server memo of the ``auto`` probe outcome (TTL = ``mcp_tool_cache_default_ttl_s``)."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._entries: dict[str, _MemoEntry] = {}

    def get(self, key: str) -> _MemoEntry | None:
        """Return the live entry for *key* (expired entries are dropped)."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= self._clock():
            del self._entries[key]
            return None
        return entry

    def put(
        self,
        key: str,
        mode: Literal["legacy", "stateless"],
        discovery: ServerDiscovery | None,
        ttl_s: float,
    ) -> None:
        """Remember *mode* for *key* for *ttl_s* seconds."""
        self._entries[key] = _MemoEntry(mode, discovery, self._clock() + ttl_s)

    def clear(self) -> None:
        """Forget every server."""
        self._entries.clear()


_DEFAULT_MEMO = ProtocolModeMemo()


def clear_protocol_memo() -> None:
    """Reset the process-wide ``auto`` probe memo."""
    _DEFAULT_MEMO.clear()


async def negotiate_stateless(
    server: MCPServerConfig,
    *,
    settings: Settings,
    input_handler: InputRequestHandler | None = None,
    org_id: str | None = None,
    client: httpx.AsyncClient | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    metrics: MetricsSink | None = None,
    memo: ProtocolModeMemo | None = None,
) -> StatelessHttpAdapter | None:
    """Resolve ``mcp_protocol_mode`` to a discovered stateless adapter, or ``None`` ⇒ legacy.

    ``legacy`` ⇒ ``None`` (no I/O). ``stateless`` ⇒ :class:`StatelessHttpAdapter` or
    :class:`McpDiscoveryError`. ``auto`` ⇒ probe ``server/discover`` once per server (memoized):
    ``-32601`` / HTTP 404 / 405 / a server not listing our version ⇒ ``None``; other probe failures
    ⇒ ``None`` without memoizing. Non-``streamable-http`` transports are always legacy. A stateless
    result counts ``prismal.mcp_protocol_negotiations_total{mode="stateless"}``; the caller counts
    the legacy outcome when it builds that path.
    """
    sink = metrics if metrics is not None else default_metrics()
    mode: McpProtocolMode = settings.mcp_protocol_mode
    http_capable = server.type == "streamable-http"

    def _stateless(discovery: ServerDiscovery | None = None) -> StatelessHttpAdapter:
        return StatelessHttpAdapter(
            server,
            settings=settings,
            client=client,
            transport=transport,
            input_handler=input_handler,
            org_id=org_id,
            metrics=sink,
            discovery=discovery,
        )

    def _chosen(adapter: StatelessHttpAdapter) -> StatelessHttpAdapter:
        sink.increment_counter("mcp_protocol_negotiations", attributes={"mode": "stateless"})
        return adapter

    if mode == "legacy" or (mode == "auto" and not http_capable):
        return None

    if mode == "stateless":
        if not http_capable:
            msg = f"MCP server {server.name!r}: stateless mode needs a streamable-http transport"
            raise McpDiscoveryError(msg)
        adapter = _stateless()
        try:
            discovery = await adapter.discover()
        except McpProtocolError as exc:
            await adapter.aclose()
            raise McpDiscoveryError(f"MCP server {server.name!r}: discovery failed: {exc}") from exc
        if not discovery.stateless:
            await adapter.aclose()
            version = settings.mcp_stateless_protocol_version
            msg = f"MCP server {server.name!r} does not support {version}"
            raise McpDiscoveryError(msg)
        return _chosen(adapter)

    # auto
    store = memo if memo is not None else _DEFAULT_MEMO
    key = f"{server.name}|{server.url}"
    ttl_s = float(settings.mcp_tool_cache_default_ttl_s)
    entry = store.get(key)
    if entry is not None:
        return _chosen(_stateless(entry.discovery)) if entry.mode == "stateless" else None

    probe = _stateless()
    try:
        discovery = await probe.discover()
    except (McpJsonRpcError, McpHttpError) as exc:
        await probe.aclose()
        legacy_signal = (
            isinstance(exc, McpJsonRpcError) and exc.code == c.ERROR_METHOD_NOT_FOUND
        ) or (isinstance(exc, McpHttpError) and exc.status_code in (404, 405))
        if legacy_signal:
            store.put(key, "legacy", None, ttl_s)
        else:
            logger.warning("mcp.protocol.probe_failed", server=server.name, error=str(exc))
        return None
    except McpProtocolError as exc:
        await probe.aclose()
        logger.warning("mcp.protocol.probe_failed", server=server.name, error=str(exc))
        return None
    if not discovery.stateless:
        await probe.aclose()
        store.put(key, "legacy", None, ttl_s)
        return None
    store.put(key, "stateless", discovery, ttl_s)
    return _chosen(probe)


async def select_adapter(
    server: MCPServerConfig,
    *,
    settings: Settings | None = None,
    input_handler: InputRequestHandler | None = None,
    org_id: str | None = None,
    client: httpx.AsyncClient | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    metrics: MetricsSink | None = None,
    memo: ProtocolModeMemo | None = None,
    connection_factory: Callable[[MCPServerConfig], Any] | None = None,
) -> McpProtocolAdapter:
    """Pick the adapter for *server* per ``settings.mcp_protocol_mode`` (see
    :func:`negotiate_stateless`); the legacy outcome wraps a connected
    :class:`MCPServerConnection` in :class:`LegacySessionAdapter`. Each selection counts
    ``prismal.mcp_protocol_negotiations_total{mode}``.
    """
    if settings is None:
        from prismal.core.config import get_settings

        settings = get_settings()
    sink = metrics if metrics is not None else default_metrics()
    adapter = await negotiate_stateless(
        server,
        settings=settings,
        input_handler=input_handler,
        org_id=org_id,
        client=client,
        transport=transport,
        metrics=sink,
        memo=memo,
    )
    if adapter is not None:
        return adapter
    factory = connection_factory
    if factory is None:
        from prismal.mcp.connection import MCPServerConnection

        factory = MCPServerConnection
    legacy = LegacySessionAdapter(factory(server))
    await legacy.connect()
    sink.increment_counter("mcp_protocol_negotiations", attributes={"mode": "legacy"})
    return legacy


__all__ = [
    "MAX_HINT_MS",
    "CacheScope",
    "LegacySessionAdapter",
    "McpHttpError",
    "McpJsonRpcError",
    "McpProtocolAdapter",
    "McpProtocolMode",
    "McpTaskHandle",
    "McpTaskStatus",
    "MetricsSink",
    "ProtocolModeMemo",
    "ServerDiscovery",
    "StatelessHttpAdapter",
    "ToolCallOutcome",
    "ToolListPage",
    "clear_protocol_memo",
    "default_metrics",
    "negotiate_stateless",
    "select_adapter",
]
