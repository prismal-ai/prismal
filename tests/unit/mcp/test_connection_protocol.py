"""MCPServerConnection protocol-mode wiring (PRT3-07 runtime, PRT4-01..05)."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import mcp.types
import pytest

from prismal.core.config import Settings
from prismal.core.exceptions import MCPToolError
from prismal.mcp.connection import MCPServerConfig, MCPServerConnection
from prismal.mcp.input_requests import McpInputRequest, McpInputResponse
from prismal.mcp.protocol import ProtocolModeMemo, ToolListPage, clear_protocol_memo

from .fake_mcp_servers import FakeLegacyHttpServer, FakeStatelessServer

pytestmark = pytest.mark.unit


class _Metrics:
    def __init__(self) -> None:
        self.counters: list[tuple[str, dict[str, Any]]] = []

    def increment_counter(
        self, metric: str, value: int = 1, attributes: dict[str, Any] | None = None
    ) -> None:
        self.counters.append((metric, dict(attributes or {})))

    def record_histogram(
        self, metric: str, value: float, attributes: dict[str, Any] | None = None
    ) -> None:
        return None


class _RecordingSession:
    log: list[str] = []

    def __init__(self, read: Any, write: Any) -> None:
        self.log.append("ClientSession")

    async def __aenter__(self) -> _RecordingSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def initialize(self) -> None:
        self.log.append("initialize")

    async def list_tools(self) -> mcp.types.ListToolsResult:
        self.log.append("list_tools")
        return mcp.types.ListToolsResult(tools=[mcp.types.Tool(name="t", inputSchema={})])

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> mcp.types.CallToolResult:
        self.log.append("call_tool")
        return mcp.types.CallToolResult(content=[mcp.types.TextContent(type="text", text="ok")])


@pytest.fixture
def recording(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    log: list[str] = []
    _RecordingSession.log = log

    async def _open(self: MCPServerConnection, stack: contextlib.AsyncExitStack) -> tuple[Any, Any]:
        log.append(f"open:{self._config.type}")
        return "r", "w"

    monkeypatch.setattr("prismal.mcp.connection.ClientSession", _RecordingSession)
    monkeypatch.setattr(MCPServerConnection, "_open_transport", _open)
    return log


@pytest.fixture(autouse=True)
def _memo() -> Any:
    clear_protocol_memo()
    yield
    clear_protocol_memo()


def _http() -> MCPServerConfig:
    return MCPServerConfig.model_validate(
        {
            "name": "remote",
            "type": "streamable-http",
            "url": "http://mcp.test/mcp",
            "retry_attempts": 1,
            "timeout_seconds": 5,
        }
    )


def _stdio() -> MCPServerConfig:
    return MCPServerConfig.model_validate(
        {"name": "local", "type": "stdio", "command": ["x"], "retry_attempts": 1}
    )


def _conn(cfg: MCPServerConfig, server: Any = None, **kw: Any) -> MCPServerConnection:
    handler = kw.pop("input_handler", None)
    return MCPServerConnection(
        cfg,
        settings=Settings(**kw),
        transport=server.transport() if server is not None else None,
        input_handler=handler,
        metrics=_Metrics(),
        memo=ProtocolModeMemo(),
    )


class TestLegacyUnchanged:
    @pytest.mark.parametrize("cfg", [_http(), _stdio()])
    async def test_legacy_mode_same_sequence_as_default(
        self, recording: list[str], cfg: MCPServerConfig
    ) -> None:
        default = MCPServerConnection(cfg)
        await default.connect()
        await default.call_tool("t", {})
        expected = list(recording)
        recording.clear()
        explicit = _conn(cfg, FakeStatelessServer(), mcp_protocol_mode="legacy")
        await explicit.connect()
        await explicit.call_tool("t", {})
        assert recording == expected
        assert explicit.tool_list_page is None

    async def test_auto_stdio_is_legacy_without_http(self, recording: list[str]) -> None:
        server = FakeStatelessServer()
        conn = _conn(_stdio(), server, mcp_protocol_mode="auto")
        await conn.connect()
        assert conn.connected
        assert "initialize" in recording
        assert server.methods == []


class TestStatelessConnection:
    async def test_stateless_lists_and_calls_through_adapter(self) -> None:
        server = FakeStatelessServer(cache_scope="private", ttl_ms=5000)
        conn = _conn(_http(), server, mcp_protocol_mode="stateless")
        await conn.connect()
        assert conn.connected
        assert all(isinstance(t, mcp.types.Tool) for t in conn.cached_tools)
        assert [t.name for t in await conn.list_tools()] == [f"tool_{i}" for i in range(5)]
        page = conn.tool_list_page
        assert isinstance(page, ToolListPage)
        assert page.ttl_ms == 5000
        assert page.cache_scope == "principal"
        result = await conn.call_tool("echo", {"a": 1})
        assert isinstance(result, mcp.types.CallToolResult)
        assert result.content[0].text == '{"a": 1}'  # type: ignore[union-attr]
        assert "initialize" not in server.methods
        await conn.disconnect()
        assert not conn.connected

    async def test_stateless_against_legacy_server_fails_connect(self) -> None:
        conn = _conn(_http(), FakeLegacyHttpServer(), mcp_protocol_mode="stateless")
        await conn.connect()
        assert not conn.connected
        assert conn.status.error

    async def test_auto_stateless(self) -> None:
        server = FakeStatelessServer()
        conn = _conn(_http(), server, mcp_protocol_mode="auto")
        await conn.connect()
        assert conn.connected
        assert conn.tool_list_page is not None
        assert "initialize" not in server.methods

    async def test_auto_legacy_server_falls_back_to_session(self, recording: list[str]) -> None:
        legacy = FakeLegacyHttpServer()
        conn = _conn(_http(), legacy, mcp_protocol_mode="auto")
        await conn.connect()
        assert conn.connected
        assert legacy.methods == ["server/discover"]
        assert recording[:3] == ["open:streamable-http", "ClientSession", "initialize"]

    async def test_is_error_result_kept(self) -> None:
        conn = _conn(_http(), FakeStatelessServer(), mcp_protocol_mode="stateless")
        await conn.connect()
        result = await conn.call_tool("fail", {})
        assert result.isError

    async def test_adapter_error_mapped_to_tool_error(self) -> None:
        conn = _conn(_http(), FakeStatelessServer(), mcp_protocol_mode="stateless")
        await conn.connect()
        with pytest.raises(MCPToolError):
            await conn.call_tool("always_ask", {})


class TestInputHandler:
    async def test_default_declines(self) -> None:
        conn = _conn(_http(), FakeStatelessServer(), mcp_protocol_mode="stateless")
        await conn.connect()
        result = await conn.call_tool("ask", {})
        assert result.content[0].text == "decline:opaque-1"  # type: ignore[union-attr]

    async def test_injected_handler(self) -> None:
        class _Accept:
            async def handle(
                self, request: McpInputRequest, *, server: str, org_id: str | None
            ) -> McpInputResponse:
                return McpInputResponse(action="accept", content={})

        conn = _conn(
            _http(), FakeStatelessServer(), mcp_protocol_mode="stateless", input_handler=_Accept()
        )
        await conn.connect()
        result = await conn.call_tool("ask", {})
        assert result.content[0].text == "accept:opaque-1"  # type: ignore[union-attr]


class TestTasks:
    async def test_task_polled_to_completion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = FakeStatelessServer(task_working_polls=2)
        conn = _conn(
            _http(),
            server,
            mcp_protocol_mode="stateless",
            mcp_tasks_enabled=True,
            mcp_task_poll_interval_s=0.001,
            mcp_task_poll_max_interval_s=0.002,
        )
        await conn.connect()
        result = await conn.call_tool("long", {})
        assert result.content[0].text == "long done"  # type: ignore[union-attr]
        assert server.task_gets == 3

    async def test_task_rejected_when_disabled(self) -> None:
        server = FakeStatelessServer()
        conn = _conn(_http(), server, mcp_protocol_mode="stateless")
        await conn.connect()
        with pytest.raises(MCPToolError):
            await conn.call_tool("long", {})

    async def test_task_timeout_maps_to_tool_error(self) -> None:
        server = FakeStatelessServer(task_working_polls=10_000)
        conn = _conn(
            _http(),
            server,
            mcp_protocol_mode="stateless",
            mcp_tasks_enabled=True,
            mcp_task_poll_interval_s=0.001,
            mcp_task_poll_max_interval_s=0.001,
            mcp_task_timeout_s=0.02,
        )
        await conn.connect()
        with pytest.raises(MCPToolError, match="timeout|exceeded"):
            await conn.call_tool("long", {})
        assert server.cancelled == ["task-1"]

    async def test_run_cancellation_cancels_task(self) -> None:
        server = FakeStatelessServer(task_working_polls=10_000)
        conn = _conn(
            _http(),
            server,
            mcp_protocol_mode="stateless",
            mcp_tasks_enabled=True,
            mcp_task_poll_interval_s=0.001,
            mcp_task_poll_max_interval_s=0.001,
        )
        await conn.connect()
        run = asyncio.create_task(conn.call_tool("long", {}))
        while server.task_gets < 2:
            await asyncio.sleep(0.001)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
        assert server.cancelled == ["task-1"]


class TestManagerPassThrough:
    async def test_manager_builds_connections_with_protocol_options(self, tmp_path: Any) -> None:
        from prismal.mcp.client import MCPClientManager

        server = FakeStatelessServer()
        config = tmp_path / "mcp.yaml"
        config.write_text(
            "servers:\n  - name: remote\n    type: streamable-http\n"
            "    url: http://mcp.test/mcp\n    retry_attempts: 1\n",
            encoding="utf-8",
        )
        manager = MCPClientManager(
            config, settings=Settings(mcp_protocol_mode="auto"), transport=server.transport()
        )
        await manager.load_from_config()
        tools = manager.get_all_langchain_tools()
        assert [t.name for t in tools][:2] == ["tool_0", "tool_1"]
        assert await tools[0].arun({}) == "{}"
        assert "initialize" not in server.methods
        await manager.close()


class TestManagerConfigEdgeCases:
    """Characterisation of pre-existing config-error branches (manager touched by PRT)."""

    @pytest.mark.parametrize(
        "text",
        ["servers: [unclosed", "- just a list", "servers: notalist", "servers:\n  - 3\n  - {}\n"],
    )
    async def test_bad_configs_leave_manager_empty(self, tmp_path: Any, text: str) -> None:
        from prismal.mcp.client import MCPClientManager

        path = tmp_path / "mcp.yaml"
        path.write_text(text, encoding="utf-8")
        manager = MCPClientManager(path)
        await manager.load_from_config()
        await manager.reload()
        assert manager.get_server_status() == []

    async def test_unreadable_config(self, tmp_path: Any) -> None:
        from prismal.mcp.client import MCPClientManager

        manager = MCPClientManager(tmp_path)  # a directory: exists() but read fails
        await manager.load_from_config()
        await manager.reload()
        assert manager.get_server_status() == []
