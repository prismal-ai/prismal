"""McpToolProvider + PRT tool-list cache / task-only filter (PRT3-07, PRT4-05, GRD-001)."""

from __future__ import annotations

import json
from typing import Any

import mcp.types
import pytest

from prismal.agents.extension.providers import McpToolProvider
from prismal.core.config import Settings
from prismal.mcp.adapter import MCPToolAdapter
from prismal.mcp.connection import MCPServerConfig
from prismal.mcp.tool_cache import McpToolListCache

pytestmark = pytest.mark.unit


class _Conn:
    def __init__(self, name: str) -> None:
        self._config = MCPServerConfig(name=name, type="stdio", command=["x"])
        self.connected = True

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> mcp.types.CallToolResult:
        if not self.connected:
            raise RuntimeError("Not connected — call connect() first.")
        return mcp.types.CallToolResult(
            content=[mcp.types.TextContent(type="text", text=f"ok:{name}")]
        )


class _Metrics:
    def __init__(self) -> None:
        self.counters: list[tuple[str, dict[str, Any]]] = []

    def increment_counter(
        self, metric: str, value: int = 1, attributes: dict[str, Any] | None = None
    ) -> None:
        self.counters.append((metric, dict(attributes or {})))


class _Manager:
    """Builds fresh adapters on every call over one live connection per server.

    Mirrors ``MCPClientManager``: the connection object lives until
    :meth:`reconnect` replaces it (``connect()`` builds a new one).
    """

    def __init__(self, servers: dict[str, list[mcp.types.Tool]]) -> None:
        self.servers = servers
        self.calls: list[list[str] | None] = []
        self.conns: dict[str, _Conn] = {}

    def reconnect(self, name: str) -> None:
        self.conns[name].connected = False
        self.conns[name] = _Conn(name)

    def get_all_langchain_tools(self, capabilities: list[str] | None = None) -> list[Any]:
        self.calls.append(capabilities)
        return [
            MCPToolAdapter(self.conns.setdefault(name, _Conn(name)), tool)  # type: ignore[arg-type]
            for name, tools in self.servers.items()
            for tool in tools
        ]


def _tool(name: str, desc: str = "d", task: str | None = None) -> mcp.types.Tool:
    execution = mcp.types.ToolExecution(taskSupport=task) if task else None  # type: ignore[arg-type]
    return mcp.types.Tool(
        name=name, description=desc, inputSchema={"type": "object"}, execution=execution
    )


def _block(tools: list[Any]) -> str:
    return json.dumps(
        [{"name": t.name, "description": t.description, "args": t.args} for t in tools],
        sort_keys=True,
    )


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _servers() -> dict[str, list[mcp.types.Tool]]:
    return {"a": [_tool("a1"), _tool("a2")], "b": [_tool("b1", task="required"), _tool("b2")]}


class TestDefaultsUnchanged:
    @pytest.mark.parametrize("settings", [None, Settings()])
    def test_output_and_calls_identical_to_legacy(self, settings: Settings | None) -> None:
        legacy_mgr, new_mgr = _Manager(_servers()), _Manager(_servers())
        legacy = McpToolProvider(legacy_mgr).get_tools(agent_name="x", capabilities=["web"])
        new = McpToolProvider(new_mgr, settings=settings).get_tools(
            agent_name="x", capabilities=["web"]
        )
        assert _block(new) == _block(legacy)
        assert [t.name for t in new] == ["a1", "a2", "b1", "b2"]
        assert new_mgr.calls == legacy_mgr.calls == [["web"]]

    def test_manager_error_still_empty(self) -> None:
        class _Boom:
            def get_all_langchain_tools(self, capabilities: list[str] | None = None) -> list[Any]:
                raise RuntimeError("x")

        provider = McpToolProvider(_Boom(), settings=Settings(mcp_tool_cache_enabled=True))  # type: ignore[arg-type]
        assert provider.get_tools(agent_name="x") == []


class TestTaskOnlyFilter:
    def test_filtered_and_counted_when_tasks_disabled(self) -> None:
        m = _Metrics()
        provider = McpToolProvider(
            _Manager(_servers()),
            settings=Settings(mcp_protocol_mode="auto"),
            metrics=m,
        )
        names = [t.name for t in provider.get_tools(agent_name="x")]
        assert names == ["a1", "a2", "b2"]
        assert ("mcp_tasks", {"status": "unavailable"}) in m.counters

    def test_kept_when_tasks_enabled(self) -> None:
        provider = McpToolProvider(
            _Manager(_servers()),
            settings=Settings(mcp_protocol_mode="auto", mcp_tasks_enabled=True),
            metrics=_Metrics(),
        )
        assert "b1" in [t.name for t in provider.get_tools(agent_name="x")]


class TestToolCache:
    def _provider(self, mgr: _Manager, cache: McpToolListCache, **kw: Any) -> McpToolProvider:
        return McpToolProvider(
            mgr,
            settings=Settings(mcp_tool_cache_enabled=True, mcp_tasks_enabled=True),
            tool_cache=cache,
            metrics=_Metrics(),
            **kw,
        )

    def _cache(self, clock: _Clock, metrics: _Metrics | None = None) -> McpToolListCache:
        return McpToolListCache(
            settings=Settings(mcp_tool_cache_default_ttl_s=60),
            clock=clock,
            metrics=metrics or _Metrics(),
        )

    def test_hundred_rebuilds_byte_identical_and_same_objects(self) -> None:
        clock = _Clock()
        provider = self._provider(_Manager(_servers()), self._cache(clock))
        first = provider.get_tools(agent_name="x")
        blocks = set()
        for i in range(100):
            clock.now = i * 0.5
            got = provider.get_tools(agent_name="x")
            assert all(a is b for a, b in zip(got, first, strict=True))
            blocks.add(_block(got))
        assert len(blocks) == 1

    def test_same_content_after_ttl_keeps_objects(self) -> None:
        clock = _Clock()
        provider = self._provider(_Manager(_servers()), self._cache(clock))
        first = provider.get_tools(agent_name="x")
        clock.now = 1000.0
        again = provider.get_tools(agent_name="x")
        assert all(a is b for a, b in zip(again, first, strict=True))

    def test_change_visible_only_after_ttl(self) -> None:
        clock = _Clock()
        cm = _Metrics()
        mgr = _Manager(_servers())
        provider = self._provider(mgr, self._cache(clock, cm))
        provider.get_tools(agent_name="x")
        mgr.servers["a"] = [_tool("a1", desc="new")]
        clock.now = 30.0
        assert "a2" in [t.name for t in provider.get_tools(agent_name="x")]
        clock.now = 61.0
        names = [t.name for t in provider.get_tools(agent_name="x")]
        assert "a2" not in names
        assert ("mcp_tool_list_changes", {"server": "a"}) in cm.counters

    async def test_reconnect_rebuilds_adapters_on_new_connection(self) -> None:
        """Finding 5: a reconnect with an unchanged tool list must not reuse dead adapters."""
        clock = _Clock()
        mgr = _Manager(_servers())
        provider = self._provider(mgr, self._cache(clock))
        first = provider.get_tools(agent_name="x")
        assert await first[0].ainvoke({}) == "ok:a1"
        mgr.reconnect("a")
        for now in (1.0, 1000.0):  # within and after the TTL
            clock.now = now
            got = provider.get_tools(agent_name="x")
            a1 = next(t for t in got if t.name == "a1")
            assert a1._connection is mgr.conns["a"]  # type: ignore[attr-defined]
            assert await a1.ainvoke({}) == "ok:a1"
        stable = provider.get_tools(agent_name="x")
        assert all(a is b for a, b in zip(stable, got, strict=True))
        # untouched server keeps its original adapters
        assert [t for t in stable if t.name == "b2"][0] is [t for t in first if t.name == "b2"][0]

    def test_principal_entries_isolated_per_tenant(self) -> None:
        clock = _Clock()
        cache = self._cache(clock)
        mgr_one = _Manager({"a": [_tool("only_o1")]})
        mgr_two = _Manager({"a": [_tool("only_o2")]})
        p1 = self._provider(mgr_one, cache, org_id="o1", principal="u")
        p2 = self._provider(mgr_two, cache, org_id="o2", principal="u")
        assert [t.name for t in p1.get_tools(agent_name="x")] == ["only_o1"]
        assert [t.name for t in p2.get_tools(agent_name="x")] == ["only_o2"]
        assert len(cache) == 2

    def test_default_cache_built_lazily_and_cap_applies(self) -> None:
        provider = McpToolProvider(
            _Manager(_servers()),
            max_tools=2,
            settings=Settings(mcp_tool_cache_enabled=True),
            metrics=_Metrics(),
        )
        assert [t.name for t in provider.get_tools(agent_name="x")] == ["a1", "a2"]

    def test_tool_without_server_passes_through(self) -> None:
        class _Bare:
            name = "bare"
            description = ""
            args: dict[str, Any] = {}

        class _Mgr:
            def get_all_langchain_tools(self, capabilities: list[str] | None = None) -> list[Any]:
                return [_Bare()]

        provider = self._provider(_Mgr(), self._cache(_Clock()))  # type: ignore[arg-type]
        assert [t.name for t in provider.get_tools(agent_name="x")] == ["bare"]


class TestDefaultBuilderWiring:
    async def test_settings_reach_mcp_provider(self, tmp_path: Any) -> None:
        from prismal.agents.extension.providers import build_default_tool_provider

        config = tmp_path / "mcp_servers.yaml"
        config.write_text("servers: {}\n", encoding="utf-8")
        settings = Settings(mcp_tool_cache_enabled=True)
        provider = await build_default_tool_provider(settings, mcp_config_path=config)
        mcp_provider = provider.providers[0]
        assert isinstance(mcp_provider, McpToolProvider)
        assert mcp_provider._cache_enabled is True
        off = await build_default_tool_provider(Settings(), mcp_config_path=config)
        assert off.providers[0]._prt_active is False  # type: ignore[attr-defined]


class TestFallbacks:
    def test_default_metrics_sink(self) -> None:
        provider = McpToolProvider(
            _Manager(_servers()), settings=Settings(mcp_protocol_mode="auto")
        )
        assert [t.name for t in provider.get_tools(agent_name="x")] == ["a1", "a2", "b2"]

    def test_cache_failure_returns_uncached_tools(self) -> None:
        class _BrokenCache:
            def get(self, *a: Any, **k: Any) -> Any:
                raise RuntimeError("cache down")

        provider = McpToolProvider(
            _Manager(_servers()),
            settings=Settings(mcp_tool_cache_enabled=True, mcp_tasks_enabled=True),
            tool_cache=_BrokenCache(),  # type: ignore[arg-type]
            metrics=_Metrics(),
        )
        assert [t.name for t in provider.get_tools(agent_name="x")] == ["a1", "a2", "b1", "b2"]


class TestStatelessHintsFeedCache:
    def _mgr(self, page: Any) -> _Manager:
        mgr = _Manager({"a": [_tool("a1")]})
        original = mgr.get_all_langchain_tools

        def _tools(capabilities: list[str] | None = None) -> list[Any]:
            tools = original(capabilities)
            for t in tools:
                t._connection.tool_list_page = page
            return tools

        mgr.get_all_langchain_tools = _tools  # type: ignore[method-assign]
        return mgr

    def _provider(self, mgr: _Manager, cache: McpToolListCache, **kw: Any) -> McpToolProvider:
        return McpToolProvider(
            mgr,
            settings=Settings(mcp_tool_cache_enabled=True),
            tool_cache=cache,
            metrics=_Metrics(),
            **kw,
        )

    def test_public_scope_shared_across_tenants(self) -> None:
        from prismal.mcp.protocol import ToolListPage

        cache = McpToolListCache(settings=Settings(), clock=_Clock(), metrics=_Metrics())
        page = ToolListPage(tools=(), ttl_ms=5000, cache_scope="shared")
        self._provider(self._mgr(page), cache, org_id="o1", principal="u").get_tools(agent_name="x")
        assert cache.get("a", org_id="o9", principal="z") is not None

    def test_ttl_zero_not_cached(self) -> None:
        from prismal.mcp.protocol import ToolListPage

        cache = McpToolListCache(settings=Settings(), clock=_Clock(), metrics=_Metrics())
        page = ToolListPage(tools=(), ttl_ms=0, cache_scope="shared")
        self._provider(self._mgr(page), cache).get_tools(agent_name="x")
        assert len(cache) == 0

    def test_ttl_from_page_bounds_entry(self) -> None:
        from prismal.mcp.protocol import ToolListPage

        clock = _Clock()
        cache = McpToolListCache(settings=Settings(), clock=clock, metrics=_Metrics())
        page = ToolListPage(tools=(), ttl_ms=2000, cache_scope="principal")
        self._provider(self._mgr(page), cache, org_id="o", principal="u").get_tools(agent_name="x")
        clock.now = 1.9
        assert cache.get("a", org_id="o", principal="u") is not None
        clock.now = 2.1
        assert cache.get("a", org_id="o", principal="u") is None


class TestDefaultBuilderForwardsProtocolSettings:
    """The host builder must hand the PRT settings to the MCP manager (PRT3-07)."""

    async def _capture(
        self, tmp_path: Any, monkeypatch: Any, settings: Settings | None
    ) -> dict[str, Any]:
        import prismal.mcp.client as mcp_client
        from prismal.agents.extension.providers import build_default_tool_provider

        config = tmp_path / "mcp_servers.yaml"
        config.write_text("servers: {}\n", encoding="utf-8")
        seen: dict[str, Any] = {}
        real = mcp_client.MCPClientManager

        class _Spy(real):  # type: ignore[misc, valid-type]
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                seen["args"], seen["kwargs"] = args, kwargs
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(mcp_client, "MCPClientManager", _Spy)
        await build_default_tool_provider(settings, mcp_config_path=config)
        return seen

    async def test_non_legacy_mode_reaches_manager(self, tmp_path: Any, monkeypatch: Any) -> None:
        settings = Settings(mcp_protocol_mode="auto")
        seen = await self._capture(tmp_path, monkeypatch, settings)
        assert seen["kwargs"].get("settings") is settings

    async def test_tasks_flag_reaches_manager(self, tmp_path: Any, monkeypatch: Any) -> None:
        settings = Settings(mcp_tasks_enabled=True)
        seen = await self._capture(tmp_path, monkeypatch, settings)
        assert seen["kwargs"].get("settings") is settings

    @pytest.mark.parametrize("settings", [None, Settings()])
    async def test_defaults_construct_manager_unchanged(
        self, tmp_path: Any, monkeypatch: Any, settings: Settings | None
    ) -> None:
        seen = await self._capture(tmp_path, monkeypatch, settings)
        assert seen["kwargs"] == {}
