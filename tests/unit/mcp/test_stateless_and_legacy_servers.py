"""PRT6-04 — fake stateless (2026-07-28) and legacy (2025-06-18) MCP servers end to end.

Both servers live in-process behind ``httpx`` mocks: the stateless one via
``httpx.MockTransport``; the legacy one via ``respx`` so the *real* ``mcp`` 1.28.1 SDK session
path (``streamablehttp_client`` + ``ClientSession``) runs unmodified against it.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
import respx

from prismal.core.config import Settings
from prismal.core.exceptions import McpTaskTimeout
from prismal.mcp.connection import MCPServerConfig
from prismal.mcp.input_requests import DeclineAllInputHandler
from prismal.mcp.protocol import (
    LegacySessionAdapter,
    ProtocolModeMemo,
    StatelessHttpAdapter,
    select_adapter,
)
from prismal.mcp.tasks import McpTaskPoller
from prismal.mcp.tool_cache import McpToolListCache

from .fake_mcp_servers import FakeStatelessServer

pytestmark = pytest.mark.e2e

LEGACY_URL = "http://legacy.test/mcp"


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


class _Audit:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def log_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append((event_type, payload))


class FakeLegacyStreamableServer:
    """A 2025-06-18 Streamable-HTTP server speaking enough for the 1.28.1 SDK client."""

    def __init__(self) -> None:
        self.methods: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return httpx.Response(405)
        body = json.loads(request.content)
        method = body.get("method", "")
        self.methods.append(method)
        req_id = body.get("id")
        headers = {"mcp-session-id": "sess-1", "content-type": "application/json"}
        if req_id is None:  # notification
            return httpx.Response(202, headers=headers)
        if method == "server/discover":
            error = {"code": -32601, "message": "Method not found"}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": req_id, "error": error})
        if method == "initialize":
            result: dict[str, Any] = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "legacy", "version": "1.0"},
            }
        elif method == "tools/list":
            result = {"tools": [{"name": "old_tool", "inputSchema": {"type": "object"}}]}
        elif method == "tools/call":
            result = {"content": [{"type": "text", "text": "legacy ok"}], "isError": False}
        else:
            error = {"code": -32601, "message": "nope"}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": req_id, "error": error})
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": req_id, "result": result}, headers=headers
        )


def _cfg(name: str, url: str) -> MCPServerConfig:
    return MCPServerConfig.model_validate(
        {
            "name": name,
            "type": "streamable-http",
            "url": url,
            "retry_attempts": 1,
            "timeout_seconds": 5,
        }
    )


async def test_auto_against_stateless_sends_zero_initialize() -> None:
    server = FakeStatelessServer()
    metrics = _Metrics()
    adapter = await select_adapter(
        _cfg("new", "http://new.test/mcp"),
        settings=Settings(mcp_protocol_mode="auto"),
        transport=server.transport(),
        metrics=metrics,
        memo=ProtocolModeMemo(),
    )
    assert isinstance(adapter, StatelessHttpAdapter)
    page = await adapter.list_tools()
    out = await adapter.call_tool("echo", {"q": 1})
    await adapter.aclose()
    assert len(page.tools) == 5
    assert out.result is not None
    assert server.methods.count("initialize") == 0
    assert ("mcp_protocol_negotiations", {"mode": "stateless"}) in metrics.counters


async def test_auto_against_legacy_2025_06_18_uses_session_path() -> None:
    legacy = FakeLegacyStreamableServer()
    metrics = _Metrics()
    with respx.mock(assert_all_called=False) as router:
        router.route(url=LEGACY_URL).mock(side_effect=legacy)
        adapter = await select_adapter(
            _cfg("old", LEGACY_URL),
            settings=Settings(mcp_protocol_mode="auto"),
            metrics=metrics,
            memo=ProtocolModeMemo(),
        )
        assert isinstance(adapter, LegacySessionAdapter)
        assert adapter.connection.connected
        page = await adapter.list_tools()
        out = await adapter.call_tool("old_tool", {})
        await adapter.aclose()
    assert [t["name"] for t in page.tools] == ["old_tool"]
    assert out.result is not None
    assert out.result["content"][0]["text"] == "legacy ok"
    assert ("mcp_protocol_negotiations", {"mode": "legacy"}) in metrics.counters
    assert legacy.methods[0] == "server/discover"
    assert legacy.methods[1:3] == ["initialize", "notifications/initialized"]


async def test_cached_tool_list_stable_across_rebuilds() -> None:
    server = FakeStatelessServer(cache_scope="private")
    adapter = StatelessHttpAdapter(
        _cfg("new", "http://new.test/mcp"),
        settings=Settings(),
        transport=server.transport(),
        metrics=_Metrics(),
    )
    cache = McpToolListCache(settings=Settings(), metrics=_Metrics())
    seen = set()
    for _ in range(100):
        got = cache.get("new", org_id="o1", principal="u1")
        if got is None:
            got = cache.put("new", await adapter.list_tools(), org_id="o1", principal="u1")
        seen.add(json.dumps(list(got.tools), sort_keys=True))
    assert len(seen) == 1
    assert server.methods.count("tools/list") == 3  # one paginated fetch
    assert cache.get("new", org_id="o2", principal="u1") is None


async def test_long_tool_completes_via_polling_and_timeout_cancels() -> None:
    settings = Settings(mcp_tasks_enabled=True, mcp_task_timeout_s=60.0)
    server = FakeStatelessServer(task_working_polls=2)
    adapter = StatelessHttpAdapter(
        _cfg("new", "http://new.test/mcp"),
        settings=settings,
        transport=server.transport(),
        metrics=_Metrics(),
    )

    async def _fast_sleep(_: float) -> None:
        await asyncio.sleep(0)

    started = await adapter.call_tool("long", {}, allow_task=True)
    assert started.task is not None
    done = await McpTaskPoller(
        adapter, settings=settings, sleep=_fast_sleep, metrics=_Metrics()
    ).wait(started.task)
    assert done.result == {"content": [{"type": "text", "text": "long done"}]}

    stuck = FakeStatelessServer(task_working_polls=10_000)
    stuck_adapter = StatelessHttpAdapter(
        _cfg("new", "http://new.test/mcp"),
        settings=settings,
        transport=stuck.transport(),
        metrics=_Metrics(),
    )
    now = [0.0]

    async def _clock_sleep(delay: float) -> None:
        now[0] += delay

    handle = (await stuck_adapter.call_tool("long", {}, allow_task=True)).task
    assert handle is not None
    with pytest.raises(McpTaskTimeout):
        await McpTaskPoller(
            stuck_adapter,
            settings=settings,
            clock=lambda: now[0],
            sleep=_clock_sleep,
            metrics=_Metrics(),
        ).wait(handle)
    assert stuck.cancelled == ["task-1"]


async def test_elicitation_declined_and_audited() -> None:
    audit = _Audit()
    server = FakeStatelessServer()
    adapter = StatelessHttpAdapter(
        _cfg("new", "http://new.test/mcp"),
        settings=Settings(),
        transport=server.transport(),
        input_handler=DeclineAllInputHandler(audit=audit),
        org_id="o1",
        metrics=_Metrics(),
    )
    out = await adapter.call_tool("ask", {})
    assert out.result is not None
    assert out.result["content"][0]["text"] == "decline:opaque-1"
    assert len(audit.events) == 1
    event, payload = audit.events[0]
    assert event == "mcp.input_request"
    assert payload["org_id"] == "o1"
    assert "Enter your API key" not in json.dumps(payload)


# ── Through the real MCPClientManager / MCPServerConnection (PRT3-07 runtime) ──


def _write_config(tmp_path: Any, name: str, url: str) -> Any:
    path = tmp_path / f"{name}.yaml"
    path.write_text(
        f"servers:\n  - name: {name}\n    type: streamable-http\n    url: {url}\n"
        "    retry_attempts: 1\n    timeout_seconds: 5\n",
        encoding="utf-8",
    )
    return path


async def test_manager_auto_against_stateless_zero_initialize(tmp_path: Any) -> None:
    from prismal.mcp.client import MCPClientManager
    from prismal.mcp.protocol import clear_protocol_memo

    clear_protocol_memo()
    server = FakeStatelessServer()
    manager = MCPClientManager(
        _write_config(tmp_path, "new", "http://new.test/mcp"),
        settings=Settings(mcp_protocol_mode="auto"),
        transport=server.transport(),
    )
    await manager.load_from_config()
    tools = {t.name: t for t in manager.get_all_langchain_tools()}
    assert sorted(tools) == [f"tool_{i}" for i in range(5)]
    assert await tools["tool_2"].arun({}) == "{}"
    await manager.close()
    clear_protocol_memo()
    assert server.methods.count("initialize") == 0
    assert server.methods[0] == "server/discover"
    assert "tools/call" in server.methods


class _RecordingLegacy(FakeLegacyStreamableServer):
    def __init__(self) -> None:
        super().__init__()
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            self.bodies.append(json.loads(request.content))
        return super().__call__(request)


async def _legacy_run(tmp_path: Any, settings: Settings | None) -> _RecordingLegacy:
    from prismal.mcp.client import MCPClientManager

    legacy = _RecordingLegacy()
    with respx.mock(assert_all_called=False) as router:
        router.route(url=LEGACY_URL).mock(side_effect=legacy)
        kwargs: dict[str, Any] = {} if settings is None else {"settings": settings}
        manager = MCPClientManager(_write_config(tmp_path, "old", LEGACY_URL), **kwargs)
        await manager.load_from_config()
        (tool,) = manager.get_all_langchain_tools()
        assert await tool.arun({}) == "legacy ok"
        await manager.close()
    return legacy


async def test_manager_legacy_mode_sends_todays_sequence(tmp_path: Any) -> None:
    default = await _legacy_run(tmp_path, None)
    explicit = await _legacy_run(tmp_path, Settings(mcp_protocol_mode="legacy"))
    assert default.methods == [
        "initialize",
        "notifications/initialized",
        "tools/list",
        "tools/call",
    ]
    assert explicit.bodies == default.bodies
