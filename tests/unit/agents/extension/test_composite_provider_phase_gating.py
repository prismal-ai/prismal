"""Unit tests: CompositeToolProvider phase-based capability narrowing (Phase LH — SPEC-LH-GAT-002)."""

from __future__ import annotations

from unittest.mock import MagicMock

from prismal.agents.extension.providers import CompositeToolProvider


def _make_provider(
    tools_by_capabilities: dict[tuple[str, ...] | None, list[MagicMock]],
) -> MagicMock:
    """A fake ToolProviderPort whose get_tools accepts (agent_name, capabilities, phase)."""

    def get_tools(
        *, agent_name: str, capabilities: list[str] | None = None, phase: str | None = None
    ):
        del agent_name, phase
        key = tuple(sorted(capabilities)) if capabilities else None
        for cap_key, tools in tools_by_capabilities.items():
            if cap_key == key:
                return tools
        return []

    provider = MagicMock()
    provider.get_tools.side_effect = get_tools
    return provider


def _tool(name: str) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    return tool


# ── Phase narrowing intersection (RF-LH-006) ─────────────────────────────────


def test_phase_map_entry_narrows_none_capabilities_to_the_override() -> None:
    narrow_tools = [_tool("file_read")]
    provider = _make_provider({("file_management", "general"): narrow_tools})
    composite = CompositeToolProvider(
        [provider], phase_capability_map={"coder": {"planning": ["general", "file_management"]}}
    )

    result = composite.get_tools(agent_name="coder", capabilities=None, phase="planning")

    assert result == narrow_tools
    call_kwargs = provider.get_tools.call_args.kwargs
    assert sorted(call_kwargs["capabilities"]) == ["file_management", "general"]


def test_phase_without_map_entry_passes_capabilities_through_unchanged() -> None:
    provider = _make_provider({("general",): [_tool("x")]})
    composite = CompositeToolProvider(
        [provider], phase_capability_map={"coder": {"planning": ["general", "file_management"]}}
    )

    composite.get_tools(agent_name="coder", capabilities=["general"], phase="executing")

    call_kwargs = provider.get_tools.call_args.kwargs
    assert call_kwargs["capabilities"] == ["general"]


def test_phase_map_intersects_with_explicit_capabilities() -> None:
    provider = _make_provider({("general",): [_tool("x")]})
    composite = CompositeToolProvider(
        [provider],
        phase_capability_map={"coder": {"planning": ["general", "file_management"]}},
    )

    composite.get_tools(
        agent_name="coder", capabilities=["general", "code_execution"], phase="planning"
    )

    call_kwargs = provider.get_tools.call_args.kwargs
    assert call_kwargs["capabilities"] == ["general"]  # code_execution not in the override


def test_no_phase_capability_map_is_unchanged() -> None:
    provider = _make_provider({("general",): [_tool("x")]})
    composite = CompositeToolProvider([provider])

    composite.get_tools(agent_name="coder", capabilities=["general"], phase="planning")

    call_kwargs = provider.get_tools.call_args.kwargs
    assert call_kwargs["capabilities"] == ["general"]


def test_phase_none_is_byte_for_byte_unchanged() -> None:
    """phase=None (the default) never touches capabilities, even with a map configured."""
    provider = _make_provider({("general",): [_tool("x")]})
    composite = CompositeToolProvider(
        [provider], phase_capability_map={"coder": {"planning": ["file_management"]}}
    )

    composite.get_tools(agent_name="coder", capabilities=["general"])

    call_kwargs = provider.get_tools.call_args.kwargs
    assert call_kwargs["capabilities"] == ["general"]
    assert "phase" not in call_kwargs or call_kwargs["phase"] is None


# ── Fail-open TypeError shim (RF-LH-008) ─────────────────────────────────────


def test_subprovider_without_phase_support_still_contributes_tools() -> None:
    """A sub-provider whose get_tools has no phase param falls back gracefully."""

    class _TwoKeywordProvider:
        def get_tools(self, *, agent_name: str, capabilities: list[str] | None = None):
            del agent_name, capabilities
            return [_tool("legacy_tool")]

    composite = CompositeToolProvider(
        [_TwoKeywordProvider()], phase_capability_map={"coder": {"planning": ["general"]}}
    )

    result = composite.get_tools(agent_name="coder", capabilities=None, phase="planning")

    assert [t.name for t in result] == ["legacy_tool"]
