"""Tests for the concrete tool providers (Fase Y2, SPEC-TPI-002..007)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from langchain_core.tools import StructuredTool

from prismal.agents.extension.ports import ToolProviderPort, conforms_to
from prismal.agents.extension.providers import (
    CompositeToolProvider,
    FakeToolProvider,
    McpToolProvider,
    SkillToolProvider,
    StubToolProvider,
    build_default_tool_provider,
)

if TYPE_CHECKING:
    from pathlib import Path

    from langchain_core.tools import BaseTool


def _make_tool(name: str) -> StructuredTool:
    def _fn() -> str:
        return "ok"

    return StructuredTool.from_function(func=_fn, name=name, description=f"tool {name}")


def _names(tools: list[BaseTool]) -> list[str]:
    return [t.name for t in tools]


class _FakeMcpManager:
    """Duck-typed stand-in for MCPClientManager."""

    def __init__(self, tools: list[BaseTool], *, raise_exc: bool = False) -> None:
        self.tools = tools
        self.raise_exc = raise_exc
        self.last_capabilities: list[str] | None | str = "unset"

    def get_all_langchain_tools(self, capabilities: list[str] | None = None) -> list[BaseTool]:
        if self.raise_exc:
            raise RuntimeError("mcp down")
        self.last_capabilities = capabilities
        return list(self.tools)


class _FakeSkillsManager:
    """Duck-typed stand-in for SkillsManager."""

    def __init__(self, tools: list[BaseTool], *, raise_exc: bool = False) -> None:
        self.tools = tools
        self.raise_exc = raise_exc

    def get_active_tools(self) -> list[BaseTool]:
        if self.raise_exc:
            raise RuntimeError("skills down")
        return list(self.tools)


class _FixedStubProvider(StubToolProvider):
    """StubToolProvider variant returning a fixed list (test seam)."""

    def __init__(self, tools: list[BaseTool]) -> None:
        super().__init__()
        self._tools = tools

    def get_tools(
        self, *, agent_name: str, capabilities: list[str] | None = None
    ) -> list[BaseTool]:
        return list(self._tools)


class _RaisingProvider:
    def get_tools(
        self, *, agent_name: str, capabilities: list[str] | None = None
    ) -> list[BaseTool]:
        raise RuntimeError("boom")


# ---------------------------------------------------------------------------
# StubToolProvider (SPEC-TPI-004)
# ---------------------------------------------------------------------------


class TestStubToolProvider:
    def test_unknown_agent_returns_empty(self) -> None:
        assert StubToolProvider().get_tools(agent_name="nope") == []

    def test_researcher_matches_static_stubs(self) -> None:
        from prismal.agents.tools import RESEARCHER_TOOLS

        tools = StubToolProvider().get_tools(agent_name="researcher")
        assert _names(tools) == _names(RESEARCHER_TOOLS)

    def test_coder_includes_sandbox_tools(self) -> None:
        from prismal.agents.tools import CODER_TOOLS
        from prismal.sandbox.tools import SANDBOX_TOOLS

        tools = StubToolProvider().get_tools(agent_name="coder")
        assert _names(tools) == _names(CODER_TOOLS + SANDBOX_TOOLS)

    def test_planner_gets_file_io_plus_cron(self) -> None:
        from prismal.agents.tools import CRON_MANAGER_TOOLS, read_file, write_file

        tools = StubToolProvider().get_tools(agent_name="planner")
        assert _names(tools) == _names([read_file, write_file, *CRON_MANAGER_TOOLS])

    def test_ml_pipeline_agents_share_ml_tools(self) -> None:
        from prismal.agents.subgraphs.ml_pipeline.tools_ml import ML_PIPELINE_TOOLS

        provider = StubToolProvider()
        for agent in (
            "data_ingester",
            "eda_analyst",
            "feature_engineer",
            "model_trainer",
            "model_evaluator",
            "model_exporter",
        ):
            assert _names(provider.get_tools(agent_name=agent)) == _names(ML_PIPELINE_TOOLS)

    def test_financial_agents_have_no_tools(self) -> None:
        provider = StubToolProvider()
        for agent in (
            "market_data_collector",
            "technical_analyst",
            "fundamental_analyst",
            "risk_sentiment_analyst",
            "report_generator",
        ):
            assert provider.get_tools(agent_name=agent) == []

    def test_fixed_tool_agents_exposed(self) -> None:
        assert StubToolProvider().fixed_tool_agents == frozenset({"cron_manager", "critic"})


# ---------------------------------------------------------------------------
# McpToolProvider (SPEC-TPI-002)
# ---------------------------------------------------------------------------


class TestMcpToolProvider:
    def test_returns_manager_tools(self) -> None:
        tools = [_make_tool("a"), _make_tool("b")]
        provider = McpToolProvider(_FakeMcpManager(tools))  # type: ignore[arg-type]
        assert _names(provider.get_tools(agent_name="coder")) == ["a", "b"]

    def test_caps_at_max_tools(self) -> None:
        tools = [_make_tool(f"t{i}") for i in range(5)]
        provider = McpToolProvider(_FakeMcpManager(tools), max_tools=3)  # type: ignore[arg-type]
        assert len(provider.get_tools(agent_name="coder")) == 3

    def test_default_cap_is_60(self) -> None:
        tools = [_make_tool(f"t{i}") for i in range(70)]
        provider = McpToolProvider(_FakeMcpManager(tools))  # type: ignore[arg-type]
        assert len(provider.get_tools(agent_name="coder")) == 60

    def test_capabilities_forwarded(self) -> None:
        manager = _FakeMcpManager([])
        provider = McpToolProvider(manager)  # type: ignore[arg-type]
        provider.get_tools(agent_name="coder", capabilities=["research"])
        assert manager.last_capabilities == ["research"]

    def test_manager_error_returns_empty(self) -> None:
        provider = McpToolProvider(_FakeMcpManager([], raise_exc=True))  # type: ignore[arg-type]
        assert provider.get_tools(agent_name="coder") == []


# ---------------------------------------------------------------------------
# SkillToolProvider (SPEC-TPI-003)
# ---------------------------------------------------------------------------


class TestSkillToolProvider:
    def test_returns_active_tools(self) -> None:
        tools = [_make_tool("skill_a")]
        provider = SkillToolProvider(_FakeSkillsManager(tools))  # type: ignore[arg-type]
        assert _names(provider.get_tools(agent_name="coder")) == ["skill_a"]

    def test_capabilities_and_agent_ignored(self) -> None:
        tools = [_make_tool("skill_a")]
        provider = SkillToolProvider(_FakeSkillsManager(tools))  # type: ignore[arg-type]
        assert _names(provider.get_tools(agent_name="x", capabilities=["y"])) == ["skill_a"]

    def test_manager_error_returns_empty(self) -> None:
        provider = SkillToolProvider(_FakeSkillsManager([], raise_exc=True))  # type: ignore[arg-type]
        assert provider.get_tools(agent_name="coder") == []

    def test_lazy_manager_constructed_per_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Parity with get_skill_tools(): a fresh SkillsManager per call."""
        import prismal.skills.manager as skills_mod

        created: list[object] = []

        class _Mgr:
            def __init__(self) -> None:
                created.append(self)

            def get_active_tools(self) -> list[BaseTool]:
                return [_make_tool("lazy_skill")]

        monkeypatch.setattr(skills_mod, "SkillsManager", _Mgr)
        provider = SkillToolProvider()

        assert _names(provider.get_tools(agent_name="coder")) == ["lazy_skill"]
        assert _names(provider.get_tools(agent_name="coder")) == ["lazy_skill"]
        assert len(created) == 2


# ---------------------------------------------------------------------------
# CompositeToolProvider (SPEC-TPI-005)
# ---------------------------------------------------------------------------


class TestCompositeToolProvider:
    def test_fixed_tool_agent_gets_only_stubs(self) -> None:
        from prismal.agents.tools import CRON_MANAGER_TOOLS

        live = FakeToolProvider(default=[_make_tool("mcp_x")])
        composite = CompositeToolProvider([live, StubToolProvider()])
        tools = composite.get_tools(agent_name="cron_manager")
        assert _names(tools) == _names(CRON_MANAGER_TOOLS)

    def test_critic_is_fixed_tool_agent(self) -> None:
        from prismal.agents.tools import CRITIC_TOOLS

        live = FakeToolProvider(default=[_make_tool("mcp_x")])
        composite = CompositeToolProvider([live, StubToolProvider()])
        assert _names(composite.get_tools(agent_name="critic")) == _names(CRITIC_TOOLS)

    def test_merge_order_live_then_stubs(self) -> None:
        mcp = FakeToolProvider(default=[_make_tool("mcp_a")])
        skills = FakeToolProvider(default=[_make_tool("skill_b")])
        stub = _FixedStubProvider([_make_tool("stub_c")])
        composite = CompositeToolProvider([mcp, skills, stub])
        assert _names(composite.get_tools(agent_name="researcher")) == [
            "mcp_a",
            "skill_b",
            "stub_c",
        ]

    def test_stub_deduped_by_live_name(self) -> None:
        mcp = FakeToolProvider(default=[_make_tool("shared"), _make_tool("mcp_only")])
        stub = _FixedStubProvider([_make_tool("shared"), _make_tool("stub_only")])
        composite = CompositeToolProvider([mcp, stub])
        names = _names(composite.get_tools(agent_name="researcher"))
        assert names == ["shared", "mcp_only", "stub_only"]

    def test_truncates_to_max_total(self) -> None:
        live = FakeToolProvider(default=[_make_tool(f"t{i}") for i in range(8)])
        stub = _FixedStubProvider([_make_tool("stub_z")])
        composite = CompositeToolProvider([live, stub], max_total=5)
        tools = composite.get_tools(agent_name="researcher")
        assert _names(tools) == ["t0", "t1", "t2", "t3", "t4"]

    def test_default_max_total_is_120(self) -> None:
        live = FakeToolProvider(default=[_make_tool(f"t{i}") for i in range(130)])
        composite = CompositeToolProvider([live, StubToolProvider()])
        assert len(composite.get_tools(agent_name="researcher")) == 120

    def test_raising_subprovider_is_skipped(self) -> None:
        ok = FakeToolProvider(default=[_make_tool("ok_a")])
        stub = _FixedStubProvider([_make_tool("stub_b")])
        composite = CompositeToolProvider([_RaisingProvider(), ok, stub])
        assert _names(composite.get_tools(agent_name="researcher")) == ["ok_a", "stub_b"]

    def test_no_stub_provider_returns_live_only(self) -> None:
        live = FakeToolProvider(default=[_make_tool("live_a")])
        composite = CompositeToolProvider([live])
        assert _names(composite.get_tools(agent_name="researcher")) == ["live_a"]
        assert composite.get_tools(agent_name="cron_manager") == []

    def test_last_stub_provider_wins_as_fallback(self) -> None:
        first_stub = _FixedStubProvider([_make_tool("old_stub")])
        last_stub = _FixedStubProvider([_make_tool("new_stub")])
        composite = CompositeToolProvider([first_stub, last_stub])
        # The earlier stub provider acts as a live source; the last one is
        # the fallback whose names get deduped against live.
        assert _names(composite.get_tools(agent_name="researcher")) == [
            "old_stub",
            "new_stub",
        ]

    def test_providers_property(self) -> None:
        stub = StubToolProvider()
        composite = CompositeToolProvider([stub])
        assert composite.providers == (stub,)


# ---------------------------------------------------------------------------
# FakeToolProvider (SPEC-TPI-006)
# ---------------------------------------------------------------------------


class TestFakeToolProvider:
    def test_mapping_lookup(self) -> None:
        tool = _make_tool("echo")
        provider = FakeToolProvider({"researcher": [tool]})
        assert provider.get_tools(agent_name="researcher") == [tool]

    def test_default_for_unmapped_agent(self) -> None:
        fallback = _make_tool("fallback")
        provider = FakeToolProvider({}, default=[fallback])
        assert provider.get_tools(agent_name="anyone") == [fallback]

    def test_empty_when_no_mapping_nor_default(self) -> None:
        assert FakeToolProvider().get_tools(agent_name="anyone") == []


# ---------------------------------------------------------------------------
# build_default_tool_provider (SPEC-TPI-007)
# ---------------------------------------------------------------------------


class TestBuildDefaultToolProvider:
    async def test_without_mcp_config_builds_skill_and_stub(self, tmp_path: Path) -> None:
        provider = await build_default_tool_provider(mcp_config_path=tmp_path / "missing.yaml")
        assert isinstance(provider, CompositeToolProvider)
        kinds = [type(p).__name__ for p in provider.providers]
        assert kinds == ["SkillToolProvider", "StubToolProvider"]

    async def test_with_mcp_config_builds_all_three(self, tmp_path: Path) -> None:
        config = tmp_path / "mcp_servers.yaml"
        config.write_text("servers: {}\n", encoding="utf-8")
        provider = await build_default_tool_provider(mcp_config_path=config)
        kinds = [type(p).__name__ for p in provider.providers]
        assert kinds == ["McpToolProvider", "SkillToolProvider", "StubToolProvider"]

    async def test_mcp_failure_omits_mcp(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import prismal.mcp.client as mcp_client

        config = tmp_path / "mcp_servers.yaml"
        config.write_text("servers: {}\n", encoding="utf-8")

        class _Boom:
            def __init__(self, *args: object, **kwargs: object) -> None:
                raise RuntimeError("cannot construct")

        monkeypatch.setattr(mcp_client, "MCPClientManager", _Boom)
        provider = await build_default_tool_provider(mcp_config_path=config)
        kinds = [type(p).__name__ for p in provider.providers]
        assert kinds == ["SkillToolProvider", "StubToolProvider"]


# ---------------------------------------------------------------------------
# Port conformance + re-exports (Y2-07)
# ---------------------------------------------------------------------------


class TestConformanceAndReExports:
    def test_all_providers_conform_to_port(self) -> None:
        providers: list[object] = [
            StubToolProvider(),
            McpToolProvider(_FakeMcpManager([])),  # type: ignore[arg-type]
            SkillToolProvider(_FakeSkillsManager([])),  # type: ignore[arg-type]
            CompositeToolProvider([StubToolProvider()]),
            FakeToolProvider(),
        ]
        for provider in providers:
            assert conforms_to(provider, ToolProviderPort), type(provider).__name__

    def test_reexported_from_extension(self) -> None:
        import prismal.agents.extension as ext

        for symbol in (
            "CompositeToolProvider",
            "FakeToolProvider",
            "McpToolProvider",
            "SkillToolProvider",
            "StubToolProvider",
            "build_default_tool_provider",
        ):
            assert symbol in ext.__all__
            assert hasattr(ext, symbol)
