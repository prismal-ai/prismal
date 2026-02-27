"""Integration tests for the REFLEXION agent pattern (AC-003-9).

Tests the Reflexion graph topology: researcher -> critic -> supervisor loop.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from langgraph.graph.state import CompiledStateGraph

from lightagent.agents.factory import AgentFactory
from lightagent.agents.patterns import AgentPattern
from lightagent.agents.state import create_initial_state

if TYPE_CHECKING:
    from pathlib import Path


def test_reflexion_graph_compiles(tmp_path: Path) -> None:
    """REFLEXION pattern graph must compile without errors."""
    factory = AgentFactory()
    db = tmp_path / "reflexion.db"
    graph = factory.build(AgentPattern.REFLEXION, checkpoint_path=db)
    assert isinstance(graph, CompiledStateGraph)


def test_reflexion_graph_has_critic_node(tmp_path: Path) -> None:
    """REFLEXION graph must contain critic node for evaluation."""
    factory = AgentFactory()
    db = tmp_path / "reflexion.db"
    graph = factory.build(AgentPattern.REFLEXION, checkpoint_path=db)
    mermaid = graph.get_graph().draw_mermaid()
    assert "critic" in mermaid


def test_reflexion_graph_has_researcher_node(tmp_path: Path) -> None:
    """REFLEXION graph must contain researcher node for generation."""
    factory = AgentFactory()
    db = tmp_path / "reflexion.db"
    graph = factory.build(AgentPattern.REFLEXION, checkpoint_path=db)
    mermaid = graph.get_graph().draw_mermaid()
    assert "researcher" in mermaid


def test_iteration_count_starts_at_zero() -> None:
    """Initial state must have iteration_count=0 for Reflexion pattern."""
    state = create_initial_state("reflexion-test")
    assert state["iteration_count"] == 0
