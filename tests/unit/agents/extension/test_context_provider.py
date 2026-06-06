"""Tests for variante B — per-context tool provider (Fase Y4, SPEC-TPI-009)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest
from langchain_core.tools import StructuredTool

from prismal.agents import graph as graph_mod
from prismal.agents import tool_registry
from prismal.agents.extension.providers import FakeToolProvider
from prismal.core.config import get_settings

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool


def _make_tool(name: str) -> StructuredTool:
    def _fn() -> str:
        return "ok"

    return StructuredTool.from_function(func=_fn, name=name, description=f"tool {name}")


def _names(tools: list[BaseTool]) -> list[str]:
    return [t.name for t in tools]


def _config_with(provider: object) -> dict[str, Any]:
    return {"configurable": {"tool_provider": provider}}


@pytest.fixture(autouse=True)
def _reset_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool_registry, "_provider", None)


# ---------------------------------------------------------------------------
# resolve_provider
# ---------------------------------------------------------------------------


class TestResolveProvider:
    def test_reads_provider_from_config(self) -> None:
        provider = FakeToolProvider()
        assert tool_registry.resolve_provider(_config_with(provider)) is provider

    def test_falls_back_to_global_when_config_missing_key(self) -> None:
        global_provider = FakeToolProvider()
        tool_registry.set_tool_provider(global_provider)
        assert tool_registry.resolve_provider({"configurable": {}}) is global_provider

    def test_falls_back_to_global_when_config_none(self) -> None:
        global_provider = FakeToolProvider()
        tool_registry.set_tool_provider(global_provider)
        assert tool_registry.resolve_provider(None) is global_provider

    def test_returns_none_when_nothing_configured(self) -> None:
        assert tool_registry.resolve_provider(None) is None

    def test_reexported_from_graph(self) -> None:
        assert graph_mod.resolve_provider is tool_registry.resolve_provider


# ---------------------------------------------------------------------------
# get_tools_for_agent_ctx
# ---------------------------------------------------------------------------


class TestGetToolsForAgentCtx:
    def test_context_provider_wins_over_global(self) -> None:
        tool_registry.set_tool_provider(FakeToolProvider(default=[_make_tool("global_t")]))
        ctx_provider = FakeToolProvider(default=[_make_tool("ctx_t")])

        tools = tool_registry.get_tools_for_agent_ctx("coder", _config_with(ctx_provider))
        assert _names(tools) == ["ctx_t"]

    def test_falls_back_to_global_provider(self) -> None:
        tool_registry.set_tool_provider(FakeToolProvider(default=[_make_tool("global_t")]))
        tools = tool_registry.get_tools_for_agent_ctx("coder", {"configurable": {}})
        assert _names(tools) == ["global_t"]

    def test_falls_back_to_stubs_without_any_provider(self) -> None:
        from prismal.agents.tools import RESEARCHER_TOOLS

        tools = tool_registry.get_tools_for_agent_ctx("researcher", None)
        assert _names(tools) == _names(RESEARCHER_TOOLS)

    def test_capabilities_forwarded_to_context_provider(self) -> None:
        captured: dict[str, object] = {}

        class _Recording:
            def get_tools(
                self,
                *,
                agent_name: str,
                capabilities: list[str] | None = None,
            ) -> list[BaseTool]:
                captured["agent"] = agent_name
                captured["caps"] = capabilities
                return []

        tool_registry.get_tools_for_agent_ctx(
            "rag_agent", _config_with(_Recording()), required_capabilities=["rag"]
        )
        assert captured == {"agent": "rag_agent", "caps": ["rag"]}


# ---------------------------------------------------------------------------
# Y4-03 — session isolation
# ---------------------------------------------------------------------------


class TestSessionIsolation:
    async def test_parallel_sessions_do_not_share_tools(self) -> None:
        provider_u = FakeToolProvider(default=[_make_tool("user_u_tool")])
        provider_v = FakeToolProvider(default=[_make_tool("user_v_tool")])

        async def _session(provider: FakeToolProvider) -> list[str]:
            # Simulate a node resolving tools inside a session turn.
            await asyncio.sleep(0)
            return _names(tool_registry.get_tools_for_agent_ctx("coder", _config_with(provider)))

        results = await asyncio.gather(
            *(_session(provider_u) for _ in range(5)),
            *(_session(provider_v) for _ in range(5)),
        )

        assert all(r == ["user_u_tool"] for r in results[:5])
        assert all(r == ["user_v_tool"] for r in results[5:])
        # No global state was mutated by per-context resolution.
        assert tool_registry.get_tool_provider() is None


# ---------------------------------------------------------------------------
# get_async_compiled_graph(tool_provider=...)
# ---------------------------------------------------------------------------


class _FakeGraph:
    """Stands in for the compiled singleton; records with_config calls."""

    def __init__(self) -> None:
        self.bound_configs: list[dict[str, Any]] = []

    def with_config(self, config: dict[str, Any]) -> _FakeGraph:
        bound = _FakeGraph()
        bound.bound_configs = [*self.bound_configs, config]
        return bound


class TestGraphToolProviderParam:
    @pytest.fixture(autouse=True)
    def _fake_singleton(self, monkeypatch: pytest.MonkeyPatch) -> _FakeGraph:
        fake = _FakeGraph()
        monkeypatch.setattr(graph_mod, "_async_graph", fake)
        self.fake = fake
        return fake

    async def test_default_returns_plain_graph(self) -> None:
        graph = await graph_mod.get_async_compiled_graph()
        assert graph is self.fake

    async def test_context_mode_binds_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(get_settings(), "tool_provider_mode", "context")
        provider = FakeToolProvider()

        graph = await graph_mod.get_async_compiled_graph(tool_provider=provider)

        assert isinstance(graph, _FakeGraph)
        assert graph is not self.fake
        assert graph.bound_configs == [{"configurable": {"tool_provider": provider}}]

    async def test_global_mode_ignores_provider(self) -> None:
        assert get_settings().tool_provider_mode == "global"
        provider = FakeToolProvider()

        graph = await graph_mod.get_async_compiled_graph(tool_provider=provider)
        assert graph is self.fake

    async def test_two_sessions_get_independent_bindings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(get_settings(), "tool_provider_mode", "context")
        provider_u = FakeToolProvider()
        provider_v = FakeToolProvider()

        graph_u = await graph_mod.get_async_compiled_graph(tool_provider=provider_u)
        graph_v = await graph_mod.get_async_compiled_graph(tool_provider=provider_v)

        assert graph_u is not graph_v
        assert graph_u.bound_configs[0]["configurable"]["tool_provider"] is provider_u
        assert graph_v.bound_configs[0]["configurable"]["tool_provider"] is provider_v
