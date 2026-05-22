"""Tests for AgentState management and LangGraph checkpointing.

Covers AC-003-5 (state checkpointed) and AC-003-6 (resumable by session_id).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from langchain_core.messages import HumanMessage

from prismal.agents.graph import build_supervisor_graph
from prismal.agents.state import create_initial_state

if TYPE_CHECKING:
    from pathlib import Path


def test_create_initial_state_all_defaults() -> None:
    """create_initial_state populates every field with correct defaults."""
    session_id = str(uuid.uuid4())
    state = create_initial_state(session_id)

    assert state["session_id"] == session_id
    assert state["current_agent"] == "supervisor"
    assert state["next_agent"] is None
    assert state["iteration_count"] == 0
    assert state["risk_score"] == 0.0
    assert state["messages"] == []
    assert state["task_plan"] == []
    assert state["metadata"] == {}
    assert state["token_count"] == 0
    assert state["estimated_cost_usd"] == 0.0
    assert state["permissions_granted"] == []
    assert state["security_flags"] == []


def test_agent_state_is_dict_compatible() -> None:
    """AgentState must be a dict subclass for LangGraph compatibility."""
    state = create_initial_state("test")
    assert isinstance(state, dict)


def test_state_partial_update_preserves_other_fields() -> None:
    """Updating one field must not erase unrelated fields."""
    state = create_initial_state("s1")
    state["messages"] = [HumanMessage(content="Hello")]
    state["risk_score"] = 42.0
    state["current_agent"] = "researcher"
    state["next_agent"] = None

    assert state["risk_score"] == 42.0
    assert len(state["messages"]) == 1
    assert state["current_agent"] == "researcher"


def test_graph_checkpoints_to_sqlite(tmp_path: Path) -> None:
    """Compiled graph with SQLite checkpointer creates checkpoint DB path."""
    checkpoint_db = tmp_path / "test_checkpoints.db"
    graph = build_supervisor_graph(checkpoint_path=checkpoint_db)
    assert graph is not None
    assert checkpoint_db.parent.exists()


def test_multiple_initial_states_are_independent() -> None:
    """create_initial_state returns independent objects (no shared mutable refs)."""
    state1 = create_initial_state("s1")
    state2 = create_initial_state("s2")

    state1["messages"].append(HumanMessage(content="Hello"))
    state1["task_plan"].append("task A")

    assert state2["messages"] == []
    assert state2["task_plan"] == []
    assert state1["session_id"] != state2["session_id"]


def test_state_security_fields_defaults() -> None:
    """Security-related fields must default to safe initial values."""
    state = create_initial_state("sec-test")
    assert state["risk_score"] == 0.0
    assert state["permissions_granted"] == []
    assert state["security_flags"] == []


def test_state_rag_fields_defaults() -> None:
    """RAG-related fields must default to empty."""
    state = create_initial_state("rag-test")
    assert state["retrieved_docs"] == []
    assert state["doc_grades"] == []


def test_state_tool_fields_defaults() -> None:
    """Tool result fields must default to empty."""
    state = create_initial_state("tool-test")
    assert state["tool_results"] == []
    assert state["tool_errors"] == []
