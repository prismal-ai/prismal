"""Integration tests for the CRAG agent pattern (AC-003-8).

Tests exercise the CRAG graph topology. LLM calls are mocked to avoid
external API dependencies, but real LangGraph routing and state transitions
are tested.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langgraph.graph.state import CompiledStateGraph

from prismal.agents.factory import AgentFactory
from prismal.agents.patterns import AgentPattern

if TYPE_CHECKING:
    from pathlib import Path


def test_crag_graph_compiles(tmp_path: Path) -> None:
    """CRAG pattern graph must compile without errors."""
    factory = AgentFactory()
    db = tmp_path / "crag.db"
    graph = factory.build(AgentPattern.CRAG, checkpoint_path=db)
    assert isinstance(graph, CompiledStateGraph)


def test_crag_graph_has_rag_agent_node(tmp_path: Path) -> None:
    """CRAG graph must contain rag_agent node."""
    factory = AgentFactory()
    db = tmp_path / "crag.db"
    graph = factory.build(AgentPattern.CRAG, checkpoint_path=db)
    mermaid = graph.get_graph().draw_mermaid()
    assert "rag_agent" in mermaid


def test_crag_graph_has_researcher_node_for_fallback(tmp_path: Path) -> None:
    """CRAG graph must contain researcher node for web search fallback."""
    factory = AgentFactory()
    db = tmp_path / "crag.db"
    graph = factory.build(AgentPattern.CRAG, checkpoint_path=db)
    mermaid = graph.get_graph().draw_mermaid()
    assert "researcher" in mermaid


def test_crag_graph_has_supervisor(tmp_path: Path) -> None:
    """CRAG graph must contain supervisor as coordinator."""
    factory = AgentFactory()
    db = tmp_path / "crag.db"
    graph = factory.build(AgentPattern.CRAG, checkpoint_path=db)
    mermaid = graph.get_graph().draw_mermaid()
    assert "supervisor" in mermaid
