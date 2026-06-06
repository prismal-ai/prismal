"""Tests for tool_registry delegation to the injected provider (Fase Y3, SPEC-TPI-008)."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

import pytest
from langchain_core.tools import StructuredTool

from prismal.agents import tool_registry
from prismal.agents.extension import providers as providers_mod
from prismal.agents.extension.providers import (
    CompositeToolProvider,
    FakeToolProvider,
    McpToolProvider,
    SkillToolProvider,
    StubToolProvider,
)
from prismal.core.config import get_settings
from prismal.core.exceptions import PrismalError, ToolProviderNotConfigured

if TYPE_CHECKING:
    from pathlib import Path

    from langchain_core.tools import BaseTool


def _make_tool(name: str) -> StructuredTool:
    def _fn() -> str:
        return "ok"

    return StructuredTool.from_function(func=_fn, name=name, description=f"tool {name}")


def _names(tools: list[BaseTool]) -> list[str]:
    return [t.name for t in tools]


@pytest.fixture(autouse=True)
def _reset_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate the injected global provider per test."""
    monkeypatch.setattr(tool_registry, "_provider", None)


class _RecordingProvider:
    """Minimal structural provider capturing the delegation arguments."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str] | None]] = []

    def get_tools(
        self,
        *,
        agent_name: str,
        capabilities: list[str] | None = None,
    ) -> list[BaseTool]:
        self.calls.append((agent_name, capabilities))
        return [_make_tool("recorded")]


class TestSetGetProvider:
    def test_round_trip(self) -> None:
        provider = FakeToolProvider()
        tool_registry.set_tool_provider(provider)
        assert tool_registry.get_tool_provider() is provider

    def test_default_is_none(self) -> None:
        assert tool_registry.get_tool_provider() is None

    def test_set_is_idempotent_last_wins(self) -> None:
        first = FakeToolProvider()
        second = FakeToolProvider()
        tool_registry.set_tool_provider(first)
        tool_registry.set_tool_provider(second)
        assert tool_registry.get_tool_provider() is second


class TestDelegation:
    def test_signature_unchanged(self) -> None:
        params = list(inspect.signature(tool_registry.get_tools_for_agent).parameters)
        assert params == ["agent_name", "required_capabilities"]

    def test_delegates_agent_name_and_capabilities(self) -> None:
        provider = _RecordingProvider()
        tool_registry.set_tool_provider(provider)
        tools = tool_registry.get_tools_for_agent("coder", required_capabilities=["code"])
        assert provider.calls == [("coder", ["code"])]
        assert _names(tools) == ["recorded"]

    def test_legacy_call_passes_none_capabilities(self) -> None:
        provider = _RecordingProvider()
        tool_registry.set_tool_provider(provider)
        tool_registry.get_tools_for_agent("researcher")
        assert provider.calls == [("researcher", None)]


class TestFallback:
    def test_no_provider_falls_back_to_stubs(self) -> None:
        from prismal.agents.tools import RESEARCHER_TOOLS

        tools = tool_registry.get_tools_for_agent("researcher")
        assert _names(tools) == _names(RESEARCHER_TOOLS)

    def test_no_provider_unknown_agent_returns_empty(self) -> None:
        assert tool_registry.get_tools_for_agent("unknown_agent") == []

    def test_strict_mode_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(get_settings(), "tool_provider_strict", True)
        with pytest.raises(ToolProviderNotConfigured, match="researcher"):
            tool_registry.get_tools_for_agent("researcher")

    def test_exception_is_prismal_error(self) -> None:
        assert issubclass(ToolProviderNotConfigured, PrismalError)
        message = str(ToolProviderNotConfigured("coder"))
        assert "set_tool_provider" in message


class TestPolicyConstantsParity:
    def test_legacy_constants_match_provider_policy(self) -> None:
        assert tool_registry._MAX_MCP_TOOLS == providers_mod._MAX_MCP_TOOLS == 60
        assert tool_registry._MAX_TOTAL_TOOLS == providers_mod._MAX_TOTAL_TOOLS == 120
        assert (
            tool_registry._FIXED_TOOL_AGENTS
            == providers_mod._FIXED_TOOL_AGENTS
            == frozenset({"cron_manager", "critic"})
        )


class TestParityWithDefaultComposite:
    """Y8-02 — the default composite reproduces the historical merge."""

    def test_merge_order_dedupe_and_stub_fallback(self) -> None:
        from prismal.agents.tools import RESEARCHER_TOOLS

        shadowed = RESEARCHER_TOOLS[0].name
        mcp = FakeToolProvider(default=[_make_tool(shadowed), _make_tool("mcp_b")])
        skills = FakeToolProvider(default=[_make_tool("skill_c")])
        composite = CompositeToolProvider([mcp, skills, StubToolProvider()])
        tool_registry.set_tool_provider(composite)

        tools = tool_registry.get_tools_for_agent("researcher")
        expected_stubs = [t.name for t in RESEARCHER_TOOLS if t.name != shadowed]
        assert _names(tools) == [shadowed, "mcp_b", "skill_c", *expected_stubs]

    def test_fixed_tool_agents_receive_only_stubs(self) -> None:
        from prismal.agents.tools import CRITIC_TOOLS, CRON_MANAGER_TOOLS

        composite = CompositeToolProvider(
            [FakeToolProvider(default=[_make_tool("live_x")]), StubToolProvider()]
        )
        tool_registry.set_tool_provider(composite)

        assert _names(tool_registry.get_tools_for_agent("cron_manager")) == _names(
            CRON_MANAGER_TOOLS
        )
        assert _names(tool_registry.get_tools_for_agent("critic")) == _names(CRITIC_TOOLS)

    def test_total_cap_enforced_via_registry(self) -> None:
        live = FakeToolProvider(default=[_make_tool(f"t{i}") for i in range(150)])
        composite = CompositeToolProvider([live, StubToolProvider()])
        tool_registry.set_tool_provider(composite)

        tools = tool_registry.get_tools_for_agent("researcher")
        assert len(tools) == tool_registry._MAX_TOTAL_TOOLS


class TestDeprecatedShims:
    async def test_init_mcp_warns_and_injects_default_provider(self, tmp_path: Path) -> None:
        with pytest.warns(DeprecationWarning, match="build_default_tool_provider"):
            await tool_registry.init_mcp(config_path=tmp_path / "missing.yaml")
        assert isinstance(tool_registry.get_tool_provider(), CompositeToolProvider)

    async def test_init_mcp_noop_when_provider_already_set(self) -> None:
        provider = FakeToolProvider()
        tool_registry.set_tool_provider(provider)
        with pytest.warns(DeprecationWarning):
            await tool_registry.init_mcp()
        assert tool_registry.get_tool_provider() is provider

    def test_get_mcp_tools_warns_and_delegates_to_mcp_subprovider(self) -> None:
        class _Mgr:
            def get_all_langchain_tools(
                self, capabilities: list[str] | None = None
            ) -> list[BaseTool]:
                return [_make_tool("mcp_tool")]

        composite = CompositeToolProvider(
            [McpToolProvider(_Mgr()), StubToolProvider()]  # type: ignore[arg-type]
        )
        tool_registry.set_tool_provider(composite)
        with pytest.warns(DeprecationWarning, match="get_mcp_tools"):
            tools = tool_registry.get_mcp_tools()
        assert _names(tools) == ["mcp_tool"]

    def test_get_mcp_tools_without_provider_returns_empty(self) -> None:
        with pytest.warns(DeprecationWarning):
            assert tool_registry.get_mcp_tools() == []

    def test_get_skill_tools_warns_and_delegates_to_skill_subprovider(self) -> None:
        class _Mgr:
            def get_active_tools(self) -> list[BaseTool]:
                return [_make_tool("skill_tool")]

        composite = CompositeToolProvider(
            [SkillToolProvider(_Mgr()), StubToolProvider()]  # type: ignore[arg-type]
        )
        tool_registry.set_tool_provider(composite)
        with pytest.warns(DeprecationWarning, match="get_skill_tools"):
            tools = tool_registry.get_skill_tools()
        assert _names(tools) == ["skill_tool"]

    def test_get_skill_tools_without_provider_returns_empty(self) -> None:
        with pytest.warns(DeprecationWarning):
            assert tool_registry.get_skill_tools() == []
