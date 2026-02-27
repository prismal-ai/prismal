"""Unit tests for the LangGraph SUPERVISOR state machine.

Tests cover:
- build_supervisor_graph returns a CompiledStateGraph
- get_compiled_graph returns a cached singleton
- The compiled graph contains the supervisor node
- The compiled graph contains all 7 sub-agent nodes
- build_supervisor_graph creates the checkpoint directory
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langgraph.graph.state import CompiledStateGraph

from lightagent.agents.graph import build_supervisor_graph, get_compiled_graph


def test_build_supervisor_graph_returns_compiled_graph(tmp_path: Path) -> None:
    """build_supervisor_graph must return a CompiledStateGraph instance."""
    db_path = tmp_path / "test.db"
    graph = build_supervisor_graph(checkpoint_path=db_path)
    assert isinstance(graph, CompiledStateGraph)


def test_get_compiled_graph_is_cached() -> None:
    """get_compiled_graph must return the same object on repeated calls."""
    graph1 = get_compiled_graph()
    graph2 = get_compiled_graph()
    assert graph1 is graph2


def test_graph_has_supervisor_node(tmp_path: Path) -> None:
    """The compiled graph must include a 'supervisor' node in its mermaid output."""
    db_path = tmp_path / "supervisor_check.db"
    graph = build_supervisor_graph(checkpoint_path=db_path)
    mermaid = graph.get_graph().draw_mermaid()
    assert "supervisor" in mermaid


def test_graph_has_all_sub_agent_nodes(tmp_path: Path) -> None:
    """All 7 sub-agent nodes must appear in the mermaid diagram of the graph."""
    db_path = tmp_path / "sub_agents.db"
    graph = build_supervisor_graph(checkpoint_path=db_path)
    mermaid = graph.get_graph().draw_mermaid()

    expected_agents = [
        "researcher",
        "coder",
        "rag_agent",
        "planner",
        "critic",
        "data_analyst",
        "file_manager",
    ]
    for agent in expected_agents:
        assert agent in mermaid, f"Expected node '{agent}' not found in mermaid output"


def test_graph_creates_checkpoint_dir(tmp_path: Path) -> None:
    """build_supervisor_graph must create the parent directory for the checkpoint DB."""
    nested_db_path = tmp_path / "nested" / "subdir" / "checkpoints.db"
    assert not nested_db_path.parent.exists()

    build_supervisor_graph(checkpoint_path=nested_db_path)

    assert nested_db_path.parent.exists()
