"""Unit tests for AgentState TypedDict and create_initial_state factory."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from lightagent.agents.state import create_initial_state


def test_agent_state_has_required_fields() -> None:
    """create_initial_state returns a state with all required fields."""
    state = create_initial_state(session_id="sess-test-001")

    assert "messages" in state
    assert "current_agent" in state
    assert "next_agent" in state
    assert "task_plan" in state
    assert "completed_tasks" in state
    assert "pending_tasks" in state
    assert "retrieved_docs" in state
    assert "doc_grades" in state
    assert "tool_results" in state
    assert "tool_errors" in state
    assert "risk_score" in state
    assert "permissions_granted" in state
    assert "security_flags" in state
    assert "session_id" in state
    assert "created_at" in state
    assert "token_count" in state
    assert "estimated_cost_usd" in state
    assert "iteration_count" in state
    assert "metadata" in state


def test_agent_state_default_values() -> None:
    """All list fields start empty; numeric fields start at zero; next_agent is None."""
    state = create_initial_state(session_id="sess-defaults")

    assert state["messages"] == []
    assert state["next_agent"] is None
    assert state["task_plan"] == []
    assert state["completed_tasks"] == []
    assert state["pending_tasks"] == []
    assert state["retrieved_docs"] == []
    assert state["doc_grades"] == []
    assert state["tool_results"] == []
    assert state["tool_errors"] == []
    assert state["risk_score"] == 0.0
    assert state["permissions_granted"] == []
    assert state["security_flags"] == []
    assert state["token_count"] == 0
    assert state["estimated_cost_usd"] == 0.0
    assert state["iteration_count"] == 0
    assert state["metadata"] == {}


def test_add_messages_annotation_works() -> None:
    """The add_messages reducer appends new messages rather than replacing the list."""
    from langgraph.graph.message import Messages, add_messages

    initial: list[BaseMessage] = []
    msg1 = HumanMessage(content="Hello")
    after_first = cast(
        "list[BaseMessage]",
        add_messages(cast("Messages", initial), cast("Messages", [msg1])),
    )
    assert len(after_first) == 1
    assert after_first[0].content == "Hello"

    msg2 = AIMessage(content="Hi there!")
    after_second = cast(
        "list[BaseMessage]",
        add_messages(cast("Messages", after_first), cast("Messages", [msg2])),
    )
    assert len(after_second) == 2
    assert after_second[1].content == "Hi there!"


def test_create_initial_state_sets_session_id() -> None:
    """session_id passed to create_initial_state is stored verbatim in the state."""
    session_id = "sess-unique-abc123"
    state = create_initial_state(session_id=session_id)
    assert state["session_id"] == session_id


def test_create_initial_state_sets_current_agent_to_supervisor() -> None:
    """current_agent must be 'supervisor' for a freshly created state."""
    state = create_initial_state(session_id="sess-supervisor-check")
    assert state["current_agent"] == "supervisor"


def test_created_at_is_iso_format() -> None:
    """created_at must be a valid ISO 8601 datetime string."""
    state = create_initial_state(session_id="sess-iso-check")
    created_at = state["created_at"]

    # Must be a string
    assert isinstance(created_at, str)

    # Must be parseable as a datetime
    parsed = datetime.fromisoformat(created_at)

    # Must be a recent timestamp (within last 5 seconds)
    now = datetime.now(UTC)
    # Handle both tz-aware and tz-naive by normalising
    if parsed.tzinfo is None:
        delta = abs((now.replace(tzinfo=None) - parsed).total_seconds())
    else:
        delta = abs((now - parsed).total_seconds())

    assert delta < 5.0, f"created_at ({created_at!r}) is not recent; delta={delta:.2f}s"


def test_create_initial_state_unique_per_call() -> None:
    """Two calls with different session_ids produce independent states."""
    state_a = create_initial_state(session_id="sess-a")
    state_b = create_initial_state(session_id="sess-b")

    assert state_a["session_id"] != state_b["session_id"]
    # Mutating one state's list should not affect the other
    state_a["task_plan"].append("task-x")
    assert state_b["task_plan"] == []


def test_agent_state_is_dict_compatible() -> None:
    """AgentState instances (TypedDicts) must be regular dicts at runtime."""
    state = create_initial_state(session_id="sess-dict-check")
    assert isinstance(state, dict)


def test_risk_score_is_float() -> None:
    """risk_score must be a float, not an int."""
    state = create_initial_state(session_id="sess-float-check")
    assert isinstance(state["risk_score"], float)


def test_estimated_cost_usd_is_float() -> None:
    """estimated_cost_usd must be a float."""
    state = create_initial_state(session_id="sess-cost-check")
    assert isinstance(state["estimated_cost_usd"], float)
