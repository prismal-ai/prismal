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

from langgraph.graph.state import CompiledStateGraph

from prismal.agents.graph import build_supervisor_graph, get_compiled_graph


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


def test_list_session_ids_returns_empty_when_no_db() -> None:
    """list_session_ids returns [] when the default checkpoint DB does not exist."""
    from pathlib import Path
    from unittest.mock import patch

    from prismal.agents.graph import list_session_ids

    with patch(
        "prismal.agents.graph._DEFAULT_CHECKPOINT_PATH",
        Path("/nonexistent/path/x.db"),
    ):
        result = list_session_ids()
    assert result == []


def test_list_session_ids_reads_thread_ids(tmp_path: Path) -> None:
    """list_session_ids returns sorted thread IDs from the SQLite checkpointer DB."""
    import sqlite3
    from unittest.mock import patch

    from prismal.agents.graph import list_session_ids

    db_path = tmp_path / "checkpoints.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE checkpoints (thread_id TEXT, checkpoint_id TEXT, checkpoint BLOB)"
        )
        conn.execute("INSERT INTO checkpoints VALUES ('sess-b', 'c1', NULL)")
        conn.execute("INSERT INTO checkpoints VALUES ('sess-a', 'c2', NULL)")
        conn.execute("INSERT INTO checkpoints VALUES ('sess-a', 'c3', NULL)")
        conn.commit()

    with patch("prismal.agents.graph._DEFAULT_CHECKPOINT_PATH", db_path):
        result = list_session_ids()

    assert result == ["sess-a", "sess-b"]


def test_list_session_ids_handles_exception(tmp_path: Path) -> None:
    """list_session_ids returns [] on any DB error (corrupt DB, etc.)."""
    from unittest.mock import patch

    from prismal.agents.graph import list_session_ids

    # Create a corrupted DB file (not a valid SQLite)
    db_path = tmp_path / "bad.db"
    db_path.write_bytes(b"THIS IS NOT A VALID SQLITE DATABASE")

    with patch("prismal.agents.graph._DEFAULT_CHECKPOINT_PATH", db_path):
        result = list_session_ids()

    assert result == []


def test_supervisor_router_wrapper_delegates(tmp_path: Path) -> None:
    """_supervisor_router delegates correctly to the underlying supervisor_router."""
    from unittest.mock import MagicMock, patch

    from prismal.agents.graph import _supervisor_router

    mock_state = MagicMock()
    with patch("prismal.agents.graph.supervisor_router", return_value="researcher") as mock_router:
        result = _supervisor_router(mock_state)

    mock_router.assert_called_once_with(mock_state)
    assert result == "researcher"
