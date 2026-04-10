"""Tests for the map-reduce parallel dispatcher (SPEC-036 / Phase 34)."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict
from unittest.mock import patch

import pytest
from langgraph.graph import StateGraph
from langgraph.types import Send

from lightagent.agents.patterns.parallel import make_parallel_dispatcher
from lightagent.agents.state import create_initial_state

# ---------------------------------------------------------------------------
# Synchronous dispatcher behaviour
# ---------------------------------------------------------------------------


def test_dispatcher_with_3_tasks_emits_3_sends() -> None:
    """3 pending tasks → 3 Send objects targeting the worker node."""
    dispatcher = make_parallel_dispatcher(
        tasks_field="pending_tasks",
        worker_node="researcher",
        max_workers=10,
    )
    state = create_initial_state(session_id="t")
    state["pending_tasks"] = ["task-a", "task-b", "task-c"]

    result = dispatcher(state)

    assert isinstance(result, list)
    assert len(result) == 3
    assert all(isinstance(s, Send) for s in result)
    assert all(s.node == "researcher" for s in result)
    assert [s.arg["_task"] for s in result] == ["task-a", "task-b", "task-c"]


def test_dispatcher_with_0_tasks_routes_to_on_empty() -> None:
    """Empty task list → string target (no Send list)."""
    dispatcher = make_parallel_dispatcher(
        tasks_field="pending_tasks",
        worker_node="researcher",
        on_empty="aggregator",
    )
    state = create_initial_state(session_id="t")
    # pending_tasks defaults to [] in create_initial_state.

    result = dispatcher(state)

    assert result == "aggregator"


def test_dispatcher_with_missing_field_routes_to_on_empty() -> None:
    """Missing tasks_field key behaves the same as an empty list."""
    dispatcher = make_parallel_dispatcher(
        tasks_field="nonexistent_key",
        worker_node="researcher",
        on_empty="aggregator",
    )
    state = create_initial_state(session_id="t")

    assert dispatcher(state) == "aggregator"


def test_dispatcher_caps_at_max_workers() -> None:
    """When tasks > max_workers, only max_workers Send objects are emitted."""
    dispatcher = make_parallel_dispatcher(
        tasks_field="pending_tasks",
        worker_node="researcher",
        max_workers=3,
    )
    state = create_initial_state(session_id="t")
    state["pending_tasks"] = ["t1", "t2", "t3", "t4", "t5"]

    result = dispatcher(state)
    assert isinstance(result, list)
    assert len(result) == 3
    assert [s.arg["_task"] for s in result] == ["t1", "t2", "t3"]


def test_dispatcher_global_settings_cap_overrides_caller() -> None:
    """settings.parallel_max_workers caps the caller-supplied value."""
    dispatcher = make_parallel_dispatcher(
        tasks_field="pending_tasks",
        worker_node="researcher",
        max_workers=20,
    )
    state = create_initial_state(session_id="t")
    state["pending_tasks"] = ["t1", "t2", "t3", "t4", "t5"]

    fake = type(
        "S",
        (),
        {"parallel_enabled": True, "parallel_max_workers": 2},
    )()
    with patch(
        "lightagent.agents.patterns.parallel.get_settings", return_value=fake
    ):
        result = dispatcher(state)

    assert isinstance(result, list)
    assert len(result) == 2  # capped by global setting, not caller's 20


def test_dispatcher_disabled_globally_routes_to_on_empty() -> None:
    """When parallel_enabled=False, dispatcher always routes to on_empty."""
    dispatcher = make_parallel_dispatcher(
        tasks_field="pending_tasks",
        worker_node="researcher",
        on_empty="fallback",
    )
    state = create_initial_state(session_id="t")
    state["pending_tasks"] = ["t1", "t2"]

    fake = type(
        "S",
        (),
        {"parallel_enabled": False, "parallel_max_workers": 10},
    )()
    with patch(
        "lightagent.agents.patterns.parallel.get_settings", return_value=fake
    ):
        result = dispatcher(state)

    assert result == "fallback"


def test_dispatcher_invalid_tasks_type_routes_to_on_empty() -> None:
    """Non-list value in tasks_field is treated as empty (logs warning)."""
    dispatcher = make_parallel_dispatcher(
        tasks_field="current_agent",  # exists but is a str, not a list
        worker_node="researcher",
        on_empty="aggregator",
    )
    state = create_initial_state(session_id="t")

    assert dispatcher(state) == "aggregator"


def test_make_parallel_dispatcher_validates_args() -> None:
    """Constructor rejects invalid configuration."""
    with pytest.raises(ValueError, match="max_workers"):
        make_parallel_dispatcher("pending_tasks", "researcher", max_workers=0)
    with pytest.raises(ValueError, match="worker_node"):
        make_parallel_dispatcher("pending_tasks", "")
    with pytest.raises(ValueError, match="tasks_field"):
        make_parallel_dispatcher("", "researcher")


def test_dispatcher_function_has_descriptive_name() -> None:
    """Returned dispatcher exposes worker_node in __name__ for tracing."""
    dispatcher = make_parallel_dispatcher("pending_tasks", "myworker")
    assert dispatcher.__name__ == "parallel_dispatcher_myworker"


# ---------------------------------------------------------------------------
# Reducer aggregation through a real LangGraph compile
# ---------------------------------------------------------------------------


class _MiniState(TypedDict):
    """Minimal state that mirrors AgentState's parallel_results reducer."""

    tasks: list[str]
    parallel_results: Annotated[list[dict[str, Any]], operator.add]


@pytest.mark.asyncio
async def test_parallel_results_aggregated_by_reducer() -> None:
    """LangGraph fan-out → workers append to parallel_results via reducer."""

    async def worker(state: _MiniState) -> dict[str, Any]:
        # Each worker invocation receives state with ``_task`` injected by
        # the dispatcher; we read it via state.get to keep the TypedDict happy.
        task = (state.get("_task") if isinstance(state, dict) else None) or "?"  # type: ignore[call-overload]
        return {"parallel_results": [{"task": task, "result": f"done:{task}"}]}

    async def aggregator(state: _MiniState) -> dict[str, Any]:
        # Aggregator runs once after all workers converge.
        return {}

    # Build a tiny dispatcher that fans out from a passthrough source node.
    async def source(state: _MiniState) -> dict[str, Any]:
        return {}

    # Local dispatcher: the make_parallel_dispatcher reads ``state["tasks"]``.
    dispatcher = make_parallel_dispatcher(
        tasks_field="tasks",
        worker_node="worker",
        max_workers=10,
        on_empty="aggregator",
    )

    # Wrap dispatcher so LangGraph can resolve type hints (state is in scope).
    def _route(state: _MiniState) -> Any:
        return dispatcher(state)

    graph: StateGraph[_MiniState, Any, Any, Any] = StateGraph(_MiniState)
    graph.add_node("source", source)
    graph.add_node("worker", worker)
    graph.add_node("aggregator", aggregator)
    graph.set_entry_point("source")
    graph.add_conditional_edges(
        "source",
        _route,
        {"worker": "worker", "aggregator": "aggregator"},
    )
    graph.add_edge("worker", "aggregator")

    compiled = graph.compile()
    result = await compiled.ainvoke(
        {"tasks": ["a", "b", "c"], "parallel_results": []}
    )

    assert len(result["parallel_results"]) == 3
    tasks_seen = {item["task"] for item in result["parallel_results"]}
    assert tasks_seen == {"a", "b", "c"}


@pytest.mark.asyncio
async def test_parallel_results_empty_when_no_tasks() -> None:
    """Dispatcher with empty tasks routes to on_empty without invoking workers."""

    worker_calls: list[str] = []

    async def worker(state: _MiniState) -> dict[str, Any]:
        worker_calls.append("called")
        return {}

    async def aggregator(state: _MiniState) -> dict[str, Any]:
        return {}

    dispatcher = make_parallel_dispatcher(
        tasks_field="tasks",
        worker_node="worker",
        on_empty="aggregator",
    )

    def _route(state: _MiniState) -> Any:
        return dispatcher(state)

    graph: StateGraph[_MiniState, Any, Any, Any] = StateGraph(_MiniState)
    graph.add_node("source", lambda _s: {})
    graph.add_node("worker", worker)
    graph.add_node("aggregator", aggregator)
    graph.set_entry_point("source")
    graph.add_conditional_edges(
        "source",
        _route,
        {"worker": "worker", "aggregator": "aggregator"},
    )
    graph.add_edge("worker", "aggregator")

    compiled = graph.compile()
    await compiled.ainvoke({"tasks": [], "parallel_results": []})

    assert worker_calls == []  # workers never invoked


# ---------------------------------------------------------------------------
# Integration: research_aggregator merges parallel_results
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_research_aggregator_merges_parallel_results() -> None:
    """research_aggregator_node consolidates parallel_results into a summary."""
    from lightagent.agents.parallel_research import research_aggregator_node

    state = create_initial_state(session_id="t")
    state["parallel_results"] = [
        {
            "agent": "parallel_researcher_worker",
            "task": "Investigate library X",
            "result": "X is a Python library that...",
        },
        {
            "agent": "parallel_researcher_worker",
            "task": "Investigate library Y",
            "result": "Y is a Rust library that...",
        },
    ]

    result = await research_aggregator_node(state)

    assert result["current_agent"] == "research_aggregator"
    assert len(result["messages"]) == 1
    msg = result["messages"][0]
    content = str(msg.content)
    assert "Investigate library X" in content
    assert "Investigate library Y" in content
    assert "X is a Python library" in content
    assert "Y is a Rust library" in content


@pytest.mark.asyncio
async def test_research_aggregator_handles_empty_results() -> None:
    """Empty parallel_results yields a sane fallback message."""
    from lightagent.agents.parallel_research import research_aggregator_node

    state = create_initial_state(session_id="t")
    # parallel_results defaults to [] in create_initial_state.

    result = await research_aggregator_node(state)

    assert result["current_agent"] == "research_aggregator"
    assert "no results" in str(result["messages"][0].content).lower()
