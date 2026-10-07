"""Tests for MCP protocol adapters + selection (SPEC-PRT-MCP-001, PRT3-02..05, PRT4-01/06)."""

from __future__ import annotations

import contextlib
from collections.abc import Mapping
from typing import Any

import httpx
import mcp.types
import pytest

from prismal.core.config import Settings
from prismal.core.exceptions import McpDiscoveryError, McpProtocolError, McpTaskError
from prismal.mcp import constants as c
from prismal.mcp.connection import MCPServerConfig, MCPServerConnection
from prismal.mcp.input_requests import McpInputRequest, McpInputResponse
from prismal.mcp.protocol import (
    LegacySessionAdapter,
    McpHttpError,
    McpJsonRpcError,
    McpProtocolAdapter,
    McpTaskHandle,
    ProtocolModeMemo,
    ServerDiscovery,
    StatelessHttpAdapter,
    ToolListPage,
    clear_protocol_memo,
    select_adapter,
)
from prismal.mcp.tasks import McpTaskPoller

from .fake_mcp_servers import FakeLegacyHttpServer, FakeStatelessServer

pytestmark = pytest.mark.unit


# ── helpers ──────────────────────────────────────────────────────────────────


class _Metrics:
    def __init__(self) -> None:
        self.counters: list[tuple[str, dict[str, Any]]] = []
        self.histograms: list[tuple[str, float]] = []

    def increment_counter(
        self, metric: str, value: int = 1, attributes: dict[str, Any] | None = None
    ) -> None:
        self.counters.append((metric, dict(attributes or {})))

    def record_histogram(
        self, metric: str, value: float, attributes: dict[str, Any] | None = None
    ) -> None:
        self.histograms.append((metric, value))


def _settings(**kw: Any) -> Settings:
    return Settings(**kw)


def _http_cfg(name: str = "remote", **kw: Any) -> MCPServerConfig:
    data: dict[str, Any] = {"name": name, "type": "streamable-http", "url": "http://mcp.test/mcp"}
    data.update(kw)
    return MCPServerConfig.model_validate(data)


def _stdio_cfg() -> MCPServerConfig:
    return MCPServerConfig.model_validate(
        {"name": "local", "type": "stdio", "command": ["python", "-m", "x"], "retry_attempts": 1}
    )


def _stateless(
    server: FakeStatelessServer, *, metrics: _Metrics | None = None, **kw: Any
) -> StatelessHttpAdapter:
    handler = kw.pop("input_handler", None)
    return StatelessHttpAdapter(
        _http_cfg(),
        settings=_settings(**kw),
        transport=server.transport(),
        input_handler=handler,
        metrics=metrics or _Metrics(),
    )


# ── Legacy adapter: byte-for-byte delegation (recording fake session) ───────


class _RecordingSession:
    log: list[tuple[str, Any]] = []

    def __init__(self, read: Any, write: Any) -> None:
        self.log.append(("ClientSession", (read, write)))

    async def __aenter__(self) -> _RecordingSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.log.append(("exit", None))

    async def initialize(self) -> None:
        self.log.append(("initialize", None))

    async def list_tools(self) -> mcp.types.ListToolsResult:
        self.log.append(("list_tools", None))
        return mcp.types.ListToolsResult(
            tools=[mcp.types.Tool(name="t", description="d", inputSchema={"type": "object"})]
        )

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> mcp.types.CallToolResult:
        self.log.append(("call_tool", (name, arguments)))
        return mcp.types.CallToolResult(
            content=[mcp.types.TextContent(type="text", text="ok")], isError=False
        )


@pytest.fixture
def recording(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    log: list[tuple[str, Any]] = []
    _RecordingSession.log = log

    async def _open(self: MCPServerConnection, stack: contextlib.AsyncExitStack) -> tuple[Any, Any]:
        return "r", "w"

    monkeypatch.setattr("prismal.mcp.connection.ClientSession", _RecordingSession)
    monkeypatch.setattr(MCPServerConnection, "_open_transport", _open)
    return log


class TestLegacySessionAdapter:
    async def test_request_sequence_identical_to_direct_connection(
        self, recording: list[tuple[str, Any]]
    ) -> None:
        direct = MCPServerConnection(_stdio_cfg())
        await direct.connect()
        await direct.list_tools()
        await direct.call_tool("t", {"a": 1})
        await direct.disconnect()
        expected = list(recording)
        recording.clear()

        adapter = LegacySessionAdapter(MCPServerConnection(_stdio_cfg()))
        await adapter.connect()
        page = await adapter.list_tools()
        out = await adapter.call_tool("t", {"a": 1}, allow_task=True)
        await adapter.aclose()
        assert recording == expected
        assert [t["name"] for t in page.tools] == ["t"]
        assert page.ttl_ms is None
        assert page.cache_scope is None
        assert out.result is not None
        assert out.result["content"][0]["text"] == "ok"
        assert out.task is None
        assert not out.is_error

    async def test_discovery_and_no_tasks(self, recording: list[tuple[str, Any]]) -> None:
        adapter = LegacySessionAdapter(MCPServerConnection(_stdio_cfg()))
        assert isinstance(adapter, McpProtocolAdapter)
        assert adapter.discovery.stateless is False
        assert adapter.discovery.protocol_version in c.LEGACY_PROTOCOL_VERSIONS
        assert adapter.discovery.server_name == "local"
        assert adapter.connection is not None
        with pytest.raises(McpTaskError):
            await adapter.get_task("x")
        with pytest.raises(McpTaskError):
            await adapter.cancel_task("x")


# ── Stateless adapter ────────────────────────────────────────────────────────


class TestStatelessWire:
    async def test_discover_no_initialize_meta_and_headers(self) -> None:
        server = FakeStatelessServer()
        adapter = _stateless(server)
        disc = await adapter.discover()
        assert disc.stateless is True
        assert disc.protocol_version == c.STATELESS_PROTOCOL_VERSION
        assert c.TASKS_EXTENSION_ID in disc.extensions
        assert disc.server_name == "fake-stateless"
        assert disc.server_version == "9.9"
        assert disc.instructions == "be nice"
        assert adapter.discovery is disc
        await adapter.list_tools()
        await adapter.call_tool("echo", {"x": 1})
        assert "initialize" not in server.methods
        for body, headers in zip(server.requests, server.headers, strict=True):
            meta = body["params"]["_meta"]
            assert meta[c.META_PROTOCOL_VERSION] == "2026-07-28"
            assert c.META_CLIENT_CAPABILITIES in meta
            assert meta[c.META_CLIENT_INFO]["name"] == "prismal"
            assert headers[c.HEADER_PROTOCOL_VERSION] == "2026-07-28"
            assert headers[c.HEADER_METHOD] == body["method"]
            assert "mcp-session-id" not in headers
        call_headers = server.headers[-1]
        assert call_headers[c.HEADER_NAME] == "echo"
        assert c.HEADER_NAME not in server.headers[0]
        await adapter.aclose()

    async def test_discovery_property_before_discover_raises(self) -> None:
        adapter = _stateless(FakeStatelessServer())
        with pytest.raises(McpDiscoveryError):
            _ = adapter.discovery

    async def test_tasks_extension_declared_only_when_enabled(self) -> None:
        server = FakeStatelessServer()
        await _stateless(server).discover()
        caps = server.requests[-1]["params"]["_meta"][c.META_CLIENT_CAPABILITIES]
        assert c.TASKS_EXTENSION_ID not in caps.get("extensions", {})
        await _stateless(server, mcp_tasks_enabled=True).discover()
        caps = server.requests[-1]["params"]["_meta"][c.META_CLIENT_CAPABILITIES]
        assert c.TASKS_EXTENSION_ID in caps["extensions"]

    async def test_bearer_auth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PRT_TEST_TOKEN", "tok")
        server = FakeStatelessServer()
        adapter = StatelessHttpAdapter(
            _http_cfg(auth={"type": "bearer", "token_env": "PRT_TEST_TOKEN"}),
            settings=_settings(),
            transport=server.transport(),
            metrics=_Metrics(),
        )
        await adapter.discover()
        assert server.headers[0]["authorization"] == "Bearer tok"

    async def test_sse_response_parsed(self) -> None:
        server = FakeStatelessServer(sse=True)
        disc = await _stateless(server).discover()
        assert disc.stateless

    async def test_old_version_only_server_not_stateless(self) -> None:
        server = FakeStatelessServer(supported_versions=("2025-11-25",))
        disc = await _stateless(server).discover()
        assert disc.stateless is False
        assert disc.protocol_version == "2025-11-25"

    async def test_injected_client_not_closed(self) -> None:
        server = FakeStatelessServer()
        client = httpx.AsyncClient(transport=server.transport())
        adapter = StatelessHttpAdapter(
            _http_cfg(), settings=_settings(), client=client, metrics=_Metrics()
        )
        await adapter.discover()
        await adapter.aclose()
        assert not client.is_closed
        await client.aclose()


class TestStatelessToolsList:
    async def test_pagination_and_cache_hints(self) -> None:
        server = FakeStatelessServer(page_size=2)
        page = await _stateless(server).list_tools()
        assert [t["name"] for t in page.tools] == [f"tool_{i}" for i in range(5)]
        assert server.methods.count("tools/list") == 3
        assert page.ttl_ms == 60_000
        assert page.cache_scope == "shared"

    async def test_private_scope_maps_to_principal(self) -> None:
        page = await _stateless(FakeStatelessServer(cache_scope="private")).list_tools()
        assert page.cache_scope == "principal"

    async def test_missing_hints(self) -> None:
        page = await _stateless(FakeStatelessServer(ttl_ms=None, cache_scope=None)).list_tools()
        assert page.ttl_ms is None
        assert page.cache_scope is None

    async def test_cursor_loop_detected(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            import json

            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {"tools": [], "nextCursor": "same"},
                },
            )

        adapter = StatelessHttpAdapter(
            _http_cfg(),
            settings=_settings(),
            transport=httpx.MockTransport(handler),
            metrics=_Metrics(),
        )
        with pytest.raises(McpProtocolError, match="cursor"):
            await adapter.list_tools()


class TestStatelessErrors:
    async def test_jsonrpc_error_mapped(self) -> None:
        legacy = FakeLegacyHttpServer()
        adapter = StatelessHttpAdapter(
            _http_cfg(), settings=_settings(), transport=legacy.transport(), metrics=_Metrics()
        )
        with pytest.raises(McpJsonRpcError) as info:
            await adapter.discover()
        assert info.value.code == c.ERROR_METHOD_NOT_FOUND
        assert isinstance(info.value, McpProtocolError)

    async def test_http_error_mapped(self) -> None:
        legacy = FakeLegacyHttpServer(mode="http", status=405)
        adapter = StatelessHttpAdapter(
            _http_cfg(), settings=_settings(), transport=legacy.transport(), metrics=_Metrics()
        )
        with pytest.raises(McpHttpError) as info:
            await adapter.discover()
        assert info.value.status_code == 405

    async def test_invalid_json_body(self) -> None:
        adapter = StatelessHttpAdapter(
            _http_cfg(),
            settings=_settings(),
            transport=httpx.MockTransport(lambda r: httpx.Response(200, text="not json")),
            metrics=_Metrics(),
        )
        with pytest.raises(McpProtocolError):
            await adapter.discover()

    async def test_transport_error_mapped(self) -> None:
        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        adapter = StatelessHttpAdapter(
            _http_cfg(),
            settings=_settings(),
            transport=httpx.MockTransport(boom),
            metrics=_Metrics(),
        )
        with pytest.raises(McpProtocolError):
            await adapter.discover()

    async def test_sse_without_matching_response(self) -> None:
        adapter = StatelessHttpAdapter(
            _http_cfg(),
            settings=_settings(),
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200, text="data: {}\n\n", headers={"content-type": "text/event-stream"}
                )
            ),
            metrics=_Metrics(),
        )
        with pytest.raises(McpProtocolError):
            await adapter.discover()


class TestStatelessCalls:
    async def test_complete_result(self) -> None:
        out = await _stateless(FakeStatelessServer()).call_tool("echo", {"b": 2})
        assert out.result is not None
        assert out.result["content"][0]["text"] == '{"b": 2}'
        assert out.task is None
        assert not out.is_error

    async def test_is_error_result(self) -> None:
        out = await _stateless(FakeStatelessServer()).call_tool("fail", {})
        assert out.is_error

    async def test_mrtr_elicitation_declined_and_retried(self) -> None:
        server = FakeStatelessServer()
        m = _Metrics()
        out = await _stateless(server, metrics=m).call_tool("ask", {})
        assert out.result is not None
        assert out.result["content"][0]["text"] == "decline:opaque-1"
        calls = [r for r in server.requests if r["method"] == "tools/call"]
        assert len(calls) == 2
        assert calls[1]["params"]["inputResponses"] == {"q": {"action": "decline"}}
        assert calls[1]["params"]["requestState"] == "opaque-1"
        assert ("mcp_input_requests", {"kind": "elicitation", "action": "decline"}) in m.counters

    async def test_mrtr_custom_handler(self) -> None:
        class _Accept:
            async def handle(
                self, request: McpInputRequest, *, server: str, org_id: str | None
            ) -> McpInputResponse:
                return McpInputResponse(action="accept", content={"k": "v"})

        out = await _stateless(FakeStatelessServer(), input_handler=_Accept()).call_tool("ask", {})
        assert out.result is not None
        assert out.result["content"][0]["text"] == "accept:opaque-1"

    async def test_mrtr_bounded_rounds(self) -> None:
        server = FakeStatelessServer()
        with pytest.raises(McpProtocolError, match="input"):
            await _stateless(server).call_tool("always_ask", {})
        calls = [r for r in server.requests if r["method"] == "tools/call"]
        assert 1 < len(calls) <= 5
        assert calls[-1]["params"]["inputResponses"] == {"r": {"action": "decline"}}


class TestStatelessTasks:
    async def test_task_rejected_when_extension_not_declared(self) -> None:
        server = FakeStatelessServer()
        with pytest.raises(McpTaskError):
            await _stateless(server).call_tool("long", {}, allow_task=True)
        assert server.cancelled == ["task-1"]

    async def test_task_rejected_when_allow_task_false(self) -> None:
        server = FakeStatelessServer()
        with pytest.raises(McpTaskError):
            await _stateless(server, mcp_tasks_enabled=True).call_tool("long", {})

    async def test_long_tool_completes_via_polling(self) -> None:
        server = FakeStatelessServer(task_working_polls=3)
        settings = _settings(mcp_tasks_enabled=True)
        adapter = StatelessHttpAdapter(
            _http_cfg(), settings=settings, transport=server.transport(), metrics=_Metrics()
        )
        out = await adapter.call_tool("long", {}, allow_task=True)
        assert out.task is not None
        assert isinstance(out.task, McpTaskHandle)
        assert out.task.task_id == "task-1"
        assert out.task.poll_hint_ms == 5
        assert out.task.server == "remote"

        slept: list[float] = []

        async def _sleep(d: float) -> None:
            slept.append(d)

        poller = McpTaskPoller(adapter, settings=settings, sleep=_sleep, metrics=_Metrics())
        final = await poller.wait(out.task)
        assert final.result == {"content": [{"type": "text", "text": "long done"}]}
        assert server.task_gets == 4
        assert len(slept) == 3

    async def test_task_input_required_answered_via_update(self) -> None:
        import json

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            if body["method"] == "tasks/get":
                result = {
                    "taskId": "t",
                    "status": "input_required",
                    "inputRequests": {
                        "e": {
                            "method": "elicitation/create",
                            "params": {"mode": "form", "message": "m"},
                        }
                    },
                }
            else:
                result = {}
            seen.append(body)
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

        seen: list[dict[str, Any]] = []
        adapter = StatelessHttpAdapter(
            _http_cfg(),
            settings=_settings(mcp_tasks_enabled=True),
            transport=httpx.MockTransport(handler),
            metrics=_Metrics(),
        )
        status = await adapter.get_task("t")
        assert status.status == "input_required"
        assert seen[-1]["method"] == "tasks/update"
        assert seen[-1]["params"]["inputResponses"] == {"e": {"action": "decline"}}

    async def test_get_task_requires_extension(self) -> None:
        with pytest.raises(McpTaskError):
            await _stateless(FakeStatelessServer()).get_task("t")

    async def test_cancel_task(self) -> None:
        server = FakeStatelessServer()
        await _stateless(server, mcp_tasks_enabled=True).cancel_task("task-9")
        assert server.cancelled == ["task-9"]


# ── select_adapter ───────────────────────────────────────────────────────────


class _FakeConnection:
    instances: list[_FakeConnection] = []

    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config
        self.connected_calls = 0
        _FakeConnection.instances.append(self)

    async def connect(self) -> None:
        self.connected_calls += 1


@pytest.fixture(autouse=True)
def _clean_memo() -> Any:
    clear_protocol_memo()
    _FakeConnection.instances = []
    yield
    clear_protocol_memo()


class TestSelectAdapter:
    async def test_legacy_mode_default(self) -> None:
        m = _Metrics()
        server = FakeStatelessServer()
        adapter = await select_adapter(
            _http_cfg(),
            settings=_settings(),
            transport=server.transport(),
            metrics=m,
            connection_factory=_FakeConnection,
        )
        assert isinstance(adapter, LegacySessionAdapter)
        assert server.methods == []
        assert _FakeConnection.instances[0].connected_calls == 1
        assert ("mcp_protocol_negotiations", {"mode": "legacy"}) in m.counters

    async def test_auto_stateless_zero_initialize(self) -> None:
        m = _Metrics()
        server = FakeStatelessServer()
        adapter = await select_adapter(
            _http_cfg(),
            settings=_settings(mcp_protocol_mode="auto"),
            transport=server.transport(),
            metrics=m,
            connection_factory=_FakeConnection,
        )
        assert isinstance(adapter, StatelessHttpAdapter)
        await adapter.list_tools()
        assert "initialize" not in server.methods
        assert ("mcp_protocol_negotiations", {"mode": "stateless"}) in m.counters
        assert _FakeConnection.instances == []

    @pytest.mark.parametrize(("mode", "status"), [("rpc", 0), ("http", 404), ("http", 405)])
    async def test_auto_legacy_fallback(self, mode: str, status: int) -> None:
        m = _Metrics()
        legacy = FakeLegacyHttpServer(mode=mode, status=status or 404)
        adapter = await select_adapter(
            _http_cfg(),
            settings=_settings(mcp_protocol_mode="auto"),
            transport=legacy.transport(),
            metrics=m,
            connection_factory=_FakeConnection,
        )
        assert isinstance(adapter, LegacySessionAdapter)
        assert ("mcp_protocol_negotiations", {"mode": "legacy"}) in m.counters

    async def test_auto_probe_memoized_with_ttl(self) -> None:
        now = [0.0]
        memo = ProtocolModeMemo(clock=lambda: now[0])
        server = FakeStatelessServer()
        settings = _settings(mcp_protocol_mode="auto", mcp_tool_cache_default_ttl_s=10)
        for _ in range(3):
            a = await select_adapter(
                _http_cfg(),
                settings=settings,
                transport=server.transport(),
                metrics=_Metrics(),
                memo=memo,
            )
            assert isinstance(a, StatelessHttpAdapter)
            assert a.discovery.stateless
        assert server.methods.count("server/discover") == 1
        now[0] = 11.0
        await select_adapter(
            _http_cfg(),
            settings=settings,
            transport=server.transport(),
            metrics=_Metrics(),
            memo=memo,
        )
        assert server.methods.count("server/discover") == 2

    async def test_auto_legacy_memoized(self) -> None:
        legacy = FakeLegacyHttpServer()
        settings = _settings(mcp_protocol_mode="auto")
        for _ in range(2):
            await select_adapter(
                _http_cfg(),
                settings=settings,
                transport=legacy.transport(),
                metrics=_Metrics(),
                connection_factory=_FakeConnection,
            )
        assert legacy.methods == ["server/discover"]

    async def test_auto_unexpected_error_falls_back_without_memo(self) -> None:
        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        settings = _settings(mcp_protocol_mode="auto")
        for _ in range(2):
            adapter = await select_adapter(
                _http_cfg(),
                settings=settings,
                transport=httpx.MockTransport(boom),
                metrics=_Metrics(),
                connection_factory=_FakeConnection,
            )
            assert isinstance(adapter, LegacySessionAdapter)
        assert len(_FakeConnection.instances) == 2

    async def test_auto_non_stateless_discover_is_legacy(self) -> None:
        server = FakeStatelessServer(supported_versions=("2025-11-25",))
        adapter = await select_adapter(
            _http_cfg(),
            settings=_settings(mcp_protocol_mode="auto"),
            transport=server.transport(),
            metrics=_Metrics(),
            connection_factory=_FakeConnection,
        )
        assert isinstance(adapter, LegacySessionAdapter)

    async def test_stdio_always_legacy(self) -> None:
        for mode in ("auto", "legacy"):
            adapter = await select_adapter(
                _stdio_cfg(),
                settings=_settings(mcp_protocol_mode=mode),
                metrics=_Metrics(),
                connection_factory=_FakeConnection,
            )
            assert isinstance(adapter, LegacySessionAdapter)

    async def test_stateless_mode_requires_stateless_server(self) -> None:
        with pytest.raises(McpDiscoveryError):
            await select_adapter(
                _stdio_cfg(), settings=_settings(mcp_protocol_mode="stateless"), metrics=_Metrics()
            )
        legacy = FakeLegacyHttpServer()
        with pytest.raises(McpDiscoveryError):
            await select_adapter(
                _http_cfg(),
                settings=_settings(mcp_protocol_mode="stateless"),
                transport=legacy.transport(),
                metrics=_Metrics(),
            )
        old = FakeStatelessServer(supported_versions=("2025-06-18",))
        with pytest.raises(McpDiscoveryError):
            await select_adapter(
                _http_cfg(),
                settings=_settings(mcp_protocol_mode="stateless"),
                transport=old.transport(),
                metrics=_Metrics(),
            )

    async def test_stateless_mode_ok(self) -> None:
        m = _Metrics()
        adapter = await select_adapter(
            _http_cfg(),
            settings=_settings(mcp_protocol_mode="stateless"),
            transport=FakeStatelessServer().transport(),
            metrics=m,
        )
        assert isinstance(adapter, StatelessHttpAdapter)
        assert ("mcp_protocol_negotiations", {"mode": "stateless"}) in m.counters


class TestValueObjects:
    def test_defaults(self) -> None:
        d = ServerDiscovery(protocol_version="x", stateless=False)
        assert d.extensions == frozenset()
        assert ToolListPage(tools=()).cache_scope is None

    def test_task_status_terminal(self) -> None:
        from prismal.mcp.protocol import McpTaskStatus

        assert McpTaskStatus(task_id="t", status="completed").terminal
        assert not McpTaskStatus(task_id="t", status="working").terminal

    def test_mapping_arguments_accepted(self) -> None:
        args: Mapping[str, Any] = {"a": 1}
        assert dict(args) == {"a": 1}
