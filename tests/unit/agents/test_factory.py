"""Unit tests for AgentFactory.

Tests cover:
- build() returns a CompiledStateGraph for each named pattern
- SUPERVISOR, REACT, REFLEXION, CRAG, PLAN_EXECUTE, CODEACT patterns build successfully
- All 8 patterns build without error (parametrised)
- CRAG graph contains rag_agent and researcher nodes
- REFLEXION graph contains critic node
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langgraph.graph.state import CompiledStateGraph

from prismal.agents.factory import AgentFactory
from prismal.agents.patterns import AgentPattern


def test_factory_build_supervisor_pattern(tmp_path: Path) -> None:
    """build() with SUPERVISOR must return a CompiledStateGraph."""
    factory = AgentFactory()
    graph = factory.build(AgentPattern.SUPERVISOR, checkpoint_path=tmp_path / "supervisor.db")
    assert isinstance(graph, CompiledStateGraph)


def test_factory_build_react_pattern(tmp_path: Path) -> None:
    """build() with REACT must return a CompiledStateGraph."""
    factory = AgentFactory()
    graph = factory.build(AgentPattern.REACT, checkpoint_path=tmp_path / "react.db")
    assert isinstance(graph, CompiledStateGraph)


def test_factory_build_reflexion_pattern(tmp_path: Path) -> None:
    """build() with REFLEXION must return a CompiledStateGraph."""
    factory = AgentFactory()
    graph = factory.build(AgentPattern.REFLEXION, checkpoint_path=tmp_path / "reflexion.db")
    assert isinstance(graph, CompiledStateGraph)


def test_factory_build_crag_pattern(tmp_path: Path) -> None:
    """build() with CRAG must return a CompiledStateGraph."""
    factory = AgentFactory()
    graph = factory.build(AgentPattern.CRAG, checkpoint_path=tmp_path / "crag.db")
    assert isinstance(graph, CompiledStateGraph)


def test_factory_build_plan_execute_pattern(tmp_path: Path) -> None:
    """build() with PLAN_EXECUTE must return a CompiledStateGraph."""
    factory = AgentFactory()
    graph = factory.build(AgentPattern.PLAN_EXECUTE, checkpoint_path=tmp_path / "plan_execute.db")
    assert isinstance(graph, CompiledStateGraph)


def test_factory_build_codeact_pattern(tmp_path: Path) -> None:
    """build() with CODEACT must return a CompiledStateGraph."""
    factory = AgentFactory()
    graph = factory.build(AgentPattern.CODEACT, checkpoint_path=tmp_path / "codeact.db")
    assert isinstance(graph, CompiledStateGraph)


@pytest.mark.parametrize(
    "pattern",
    # DEV_PIPELINE is a dynamic subgraph (Phase 24) — not built by AgentFactory
    [p for p in AgentPattern if p != AgentPattern.DEV_PIPELINE],
)
def test_factory_supports_all_patterns(tmp_path: Path, pattern: AgentPattern) -> None:
    """Every AgentFactory-managed pattern must build successfully without raising."""
    factory = AgentFactory()
    graph = factory.build(pattern, checkpoint_path=tmp_path / f"{pattern.value}.db")
    assert isinstance(graph, CompiledStateGraph)


def test_crag_graph_has_rag_and_researcher_nodes(tmp_path: Path) -> None:
    """CRAG graph mermaid output must contain 'rag_agent' and 'researcher'."""
    factory = AgentFactory()
    graph = factory.build(AgentPattern.CRAG, checkpoint_path=tmp_path / "crag_nodes.db")
    mermaid = graph.get_graph().draw_mermaid()
    assert "rag_agent" in mermaid, "Expected 'rag_agent' node in CRAG mermaid output"
    assert "researcher" in mermaid, "Expected 'researcher' node in CRAG mermaid output"


def test_reflexion_graph_has_critic_node(tmp_path: Path) -> None:
    """REFLEXION graph mermaid output must contain 'critic'."""
    factory = AgentFactory()
    graph = factory.build(AgentPattern.REFLEXION, checkpoint_path=tmp_path / "reflexion_nodes.db")
    mermaid = graph.get_graph().draw_mermaid()
    assert "critic" in mermaid, "Expected 'critic' node in REFLEXION mermaid output"
