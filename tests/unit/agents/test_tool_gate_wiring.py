"""Mask-mode wiring helpers (Phase TKE — TKE3-02/03 wiring)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from prismal.agents.extension.providers import CompositeToolProvider, build_default_tool_provider
from prismal.agents.state import create_initial_state
from prismal.agents.tool_registry import tool_gate_react_kwargs
from prismal.core.config import Settings


def _tool(name: str) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    return tool


def _composite(mode: str) -> CompositeToolProvider:
    provider = MagicMock()

    def get_tools(*, agent_name: str, capabilities: list[str] | None = None, phase: object = None):
        del agent_name, phase
        if capabilities == ["file_management"]:
            return [_tool("file_read")]
        return [_tool("file_read"), _tool("web_search")]

    provider.get_tools.side_effect = get_tools
    return CompositeToolProvider(
        [provider],
        phase_capability_map={"coder": {"planning": ["file_management"]}},
        tool_gating_mode=mode,
    )


def _planning_state() -> dict[str, object]:
    state = create_initial_state("s-gate")
    state["metadata"] = {"loop": {"phase": "planning"}}
    return state  # type: ignore[return-value]


def test_kwargs_empty_when_master_off() -> None:
    kwargs = tool_gate_react_kwargs(
        _planning_state(), agent_name="coder", provider=_composite("mask"), settings=Settings()
    )
    assert kwargs == {}


def test_kwargs_empty_in_remove_mode() -> None:
    settings = Settings(token_efficiency_enabled=True, tool_gating_mode="remove")
    kwargs = tool_gate_react_kwargs(
        _planning_state(), agent_name="coder", provider=_composite("remove"), settings=settings
    )
    assert kwargs == {}


def test_kwargs_carry_allow_set_in_mask_mode() -> None:
    settings = Settings(token_efficiency_enabled=True, tool_gating_mode="mask")
    kwargs = tool_gate_react_kwargs(
        _planning_state(), agent_name="coder", provider=_composite("mask"), settings=settings
    )
    assert kwargs == {"tool_allow_set": frozenset({"file_read"}), "tool_phase": "planning"}


def test_kwargs_empty_without_phase() -> None:
    settings = Settings(token_efficiency_enabled=True, tool_gating_mode="mask")
    state = create_initial_state("s-nophase")
    kwargs = tool_gate_react_kwargs(
        state, agent_name="coder", provider=_composite("mask"), settings=settings
    )
    assert kwargs == {}


async def test_default_builder_unchanged_without_settings(tmp_path: Path) -> None:
    provider = await build_default_tool_provider(mcp_config_path=tmp_path / "none.yaml")
    assert provider._tool_gating_mode == "remove"
    assert provider._phase_capability_map == {}


async def test_default_builder_wires_mask_mode(tmp_path: Path) -> None:
    phases = tmp_path / "phases.yaml"
    phases.write_text("coder:\n  planning: [file_management]\n")
    settings = Settings(
        token_efficiency_enabled=True,
        tool_gating_enabled=True,
        tool_gating_mode="mask",
        tool_gating_phase_map_path=str(phases),
    )
    provider = await build_default_tool_provider(settings, mcp_config_path=tmp_path / "none.yaml")
    assert provider._tool_gating_mode == "mask"
    assert provider._phase_capability_map == {"coder": {"planning": ["file_management"]}}


def test_kwargs_fail_closed_when_allow_set_raises() -> None:
    settings = Settings(token_efficiency_enabled=True, tool_gating_mode="mask")
    provider = MagicMock()
    provider.allowed_tool_names.side_effect = RuntimeError("boom")
    kwargs = tool_gate_react_kwargs(
        _planning_state(), agent_name="coder", provider=provider, settings=settings
    )
    assert kwargs == {"tool_allow_set": frozenset(), "tool_phase": "planning"}
