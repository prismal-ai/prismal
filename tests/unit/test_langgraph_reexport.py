"""Tests for the official LangGraph re-export surface (SPEC-EXT-001)."""

from __future__ import annotations

from importlib.metadata import version

import pytest


class TestReexportIdentity:
    """Each symbol must be identical to its upstream LangGraph origin."""

    def test_state_graph_identity(self) -> None:
        from langgraph.graph import StateGraph

        import prismal.langgraph as pl

        assert pl.StateGraph is StateGraph

    def test_start_end_identity(self) -> None:
        from langgraph.graph import END, START

        import prismal.langgraph as pl

        assert pl.START is START
        assert pl.END is END

    def test_send_identity(self) -> None:
        from langgraph.types import Send

        import prismal.langgraph as pl

        assert pl.Send is Send

    def test_add_messages_identity(self) -> None:
        from langgraph.graph.message import add_messages

        import prismal.langgraph as pl

        assert pl.add_messages is add_messages

    def test_interrupt_identity(self) -> None:
        from langgraph.types import interrupt

        import prismal.langgraph as pl

        assert pl.interrupt is interrupt

    def test_compiled_state_graph_identity(self) -> None:
        from langgraph.graph.state import CompiledStateGraph

        import prismal.langgraph as pl

        assert pl.CompiledStateGraph is CompiledStateGraph


class TestReexportPrismalSymbols:
    def test_agent_state_identity(self) -> None:
        import prismal.langgraph as pl
        from prismal.agents.state import AgentState

        assert pl.AgentState is AgentState

    def test_subgraph_definition_and_registry_identity(self) -> None:
        import prismal.langgraph as pl
        from prismal.agents.subgraphs.registry import (
            SubgraphDefinition,
            SubgraphRegistry,
        )

        assert pl.SubgraphDefinition is SubgraphDefinition
        assert pl.SubgraphRegistry is SubgraphRegistry


class TestVersion:
    def test_version_matches_installed_langgraph(self) -> None:
        import prismal.langgraph as pl

        assert version("langgraph") == pl.VERSION

    def test_version_is_non_empty_string(self) -> None:
        import prismal.langgraph as pl

        assert isinstance(pl.VERSION, str)
        assert pl.VERSION


class TestPublicApi:
    def test_all_lists_every_public_symbol(self) -> None:
        import prismal.langgraph as pl

        expected = {
            "StateGraph",
            "START",
            "END",
            "Send",
            "interrupt",
            "add_messages",
            "CompiledStateGraph",
            "AgentState",
            "SubgraphDefinition",
            "SubgraphRegistry",
            "VERSION",
            # V3: graph visualization helpers re-exported as public surface.
            "to_mermaid",
            "to_mermaid_png",
            "visualize",
            "save_graph_image",
        }
        assert set(pl.__all__) == expected

    @pytest.mark.parametrize(
        "symbol",
        [
            "StateGraph",
            "START",
            "END",
            "Send",
            "interrupt",
            "add_messages",
            "CompiledStateGraph",
            "AgentState",
            "SubgraphDefinition",
            "SubgraphRegistry",
            "VERSION",
        ],
    )
    def test_every_exported_symbol_is_importable(self, symbol: str) -> None:
        import prismal.langgraph as pl

        assert hasattr(pl, symbol)
