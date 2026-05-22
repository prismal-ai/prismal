"""Tests for the HITL approval gate (SPEC-037 / Phase 35)."""

from __future__ import annotations

from typing import Annotated, Any, TypedDict
from unittest.mock import patch

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph
from langgraph.types import Command

from prismal.agents.subgraphs.gates import (
    hitl_gate,
    human_approval_node,
    seed_hitl_metadata,
)

# ---------------------------------------------------------------------------
# Synchronous unit tests for the gate routing function
# ---------------------------------------------------------------------------


def test_hitl_gate_routes_approve_to_on_approve() -> None:
    """When _hitl_last_action == 'approve', the gate routes to on_approve."""
    gate = hitl_gate(
        artifact_field="dev_pipeline.code_artifact",
        on_approve="finalise",
        on_reject="developer",
    )
    state = {"metadata": {"_hitl_last_action": "approve"}}
    assert gate(state) == "finalise"


def test_hitl_gate_routes_reject_to_on_reject() -> None:
    """reject → on_reject."""
    gate = hitl_gate("foo.bar", "on_pass_node", "developer")
    state = {"metadata": {"_hitl_last_action": "reject"}}
    assert gate(state) == "developer"


def test_hitl_gate_routes_request_changes_to_on_reject() -> None:
    """request_changes is treated like reject (loops back to generator)."""
    gate = hitl_gate("foo.bar", "on_pass_node", "developer")
    state = {"metadata": {"_hitl_last_action": "request_changes"}}
    assert gate(state) == "developer"


def test_hitl_gate_no_action_defaults_to_reject() -> None:
    """Missing _hitl_last_action defaults defensively to on_reject."""
    gate = hitl_gate("foo.bar", "on_pass_node", "reject_node")
    state = {"metadata": {}}
    assert gate(state) == "reject_node"


def test_hitl_gate_bypass_when_hitl_disabled() -> None:
    """LIGHTAGENT_HITL_ENABLED=false → gate routes directly to on_approve."""
    gate = hitl_gate("foo.bar", "approved", "rejected")
    state = {"metadata": {"_hitl_last_action": "reject"}}

    fake = type("S", (), {"hitl_enabled": False})()
    with patch(
        "lightagent.agents.subgraphs.gates.get_settings",
        return_value=fake,
    ):
        # Should bypass even though the recorded action was "reject".
        assert gate(state) == "approved"


def test_hitl_gate_bypass_via_callback() -> None:
    """bypass_condition=True → gate routes directly to on_approve."""
    gate = hitl_gate(
        artifact_field="foo",
        on_approve="approved",
        on_reject="rejected",
        bypass_condition=lambda _s: True,
    )
    state = {"metadata": {"_hitl_last_action": "reject"}}
    assert gate(state) == "approved"


def test_hitl_gate_function_has_descriptive_name() -> None:
    """The returned routing function exposes the artifact field in __name__."""
    gate = hitl_gate("dev_pipeline.code_artifact", "ok", "no")
    assert gate.__name__ == "hitl_gate_dev_pipeline_code_artifact"


def test_seed_hitl_metadata_writes_artifact_and_clears_action() -> None:
    """seed_hitl_metadata populates routing keys and clears stale action."""
    seed = seed_hitl_metadata("dev_pipeline.code_artifact", "MEDIUM")
    state = {"metadata": {"_hitl_last_action": "stale", "other": 1}}
    update = seed(state)

    meta = update["metadata"]
    assert meta["_hitl_artifact_field"] == "dev_pipeline.code_artifact"
    assert meta["_hitl_risk_level"] == "MEDIUM"
    assert "_hitl_last_action" not in meta  # cleared
    assert meta["other"] == 1  # preserved


# ---------------------------------------------------------------------------
# End-to-end integration via a real LangGraph compile
# ---------------------------------------------------------------------------


class _MiniState(TypedDict):
    """Minimal state mirroring AgentState's metadata field."""

    metadata: Annotated[dict[str, Any], lambda a, b: {**a, **b}]


@pytest.mark.asyncio
async def test_interrupt_called_on_high_risk_and_resume_approve() -> None:
    """Full pause → resume cycle: interrupt fires, then approve completes."""

    async def end_node(state: _MiniState) -> dict[str, Any]:
        return {"metadata": {"reached": "approved"}}

    async def reject_node(state: _MiniState) -> dict[str, Any]:
        return {"metadata": {"reached": "rejected"}}

    seed = seed_hitl_metadata("review_result", "HIGH")
    gate = hitl_gate(
        artifact_field="review_result",
        on_approve="end_node",
        on_reject="reject_node",
    )

    def _route(state: _MiniState) -> str:
        return gate(state)

    graph: StateGraph[_MiniState, Any, Any, Any] = StateGraph(_MiniState)
    graph.add_node("seed", seed)
    graph.add_node("human_approval", human_approval_node)
    graph.add_node("end_node", end_node)
    graph.add_node("reject_node", reject_node)
    graph.set_entry_point("seed")
    graph.add_edge("seed", "human_approval")
    graph.add_conditional_edges(
        "human_approval",
        _route,
        {"end_node": "end_node", "reject_node": "reject_node"},
    )

    checkpointer = MemorySaver()
    compiled = graph.compile(checkpointer=checkpointer)

    config = {"configurable": {"thread_id": "t-approve"}}
    initial = {"metadata": {"review_result": {"score": 0.9}}}

    # First invocation should pause at the interrupt — does NOT raise.
    result = await compiled.ainvoke(initial, config)
    snapshot = await compiled.aget_state(config)

    # The workflow is suspended at human_approval (next is non-empty).
    assert snapshot.next, "graph should be paused at the interrupt point"
    assert "reached" not in result.get("metadata", {})

    # Resume with approve.
    final = await compiled.ainvoke(
        Command(resume={"action": "approve", "modifications": {}}),
        config,
    )
    assert final["metadata"]["reached"] == "approved"


@pytest.mark.asyncio
async def test_resume_reject_routes_to_on_reject() -> None:
    """Reject decision routes the workflow to on_reject."""

    async def end_node(state: _MiniState) -> dict[str, Any]:
        return {"metadata": {"reached": "approved"}}

    async def reject_node(state: _MiniState) -> dict[str, Any]:
        return {"metadata": {"reached": "rejected"}}

    seed = seed_hitl_metadata("review_result", "HIGH")
    gate = hitl_gate("review_result", "end_node", "reject_node")

    def _route(state: _MiniState) -> str:
        return gate(state)

    graph: StateGraph[_MiniState, Any, Any, Any] = StateGraph(_MiniState)
    graph.add_node("seed", seed)
    graph.add_node("human_approval", human_approval_node)
    graph.add_node("end_node", end_node)
    graph.add_node("reject_node", reject_node)
    graph.set_entry_point("seed")
    graph.add_edge("seed", "human_approval")
    graph.add_conditional_edges(
        "human_approval",
        _route,
        {"end_node": "end_node", "reject_node": "reject_node"},
    )

    compiled = graph.compile(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "t-reject"}}

    await compiled.ainvoke({"metadata": {}}, config)
    final = await compiled.ainvoke(
        Command(resume={"action": "reject", "modifications": {}}),
        config,
    )
    assert final["metadata"]["reached"] == "rejected"


@pytest.mark.asyncio
async def test_resume_request_changes_merges_modifications() -> None:
    """request_changes merges modifications into metadata before re-routing."""

    async def end_node(state: _MiniState) -> dict[str, Any]:
        return {"metadata": {"reached": "approved"}}

    async def reject_node(state: _MiniState) -> dict[str, Any]:
        return {
            "metadata": {
                "reached": "rejected",
                "saw_revision": state["metadata"].get("revision_note", ""),
            },
        }

    seed = seed_hitl_metadata("review_result", "HIGH")
    gate = hitl_gate("review_result", "end_node", "reject_node")

    def _route(state: _MiniState) -> str:
        return gate(state)

    graph: StateGraph[_MiniState, Any, Any, Any] = StateGraph(_MiniState)
    graph.add_node("seed", seed)
    graph.add_node("human_approval", human_approval_node)
    graph.add_node("end_node", end_node)
    graph.add_node("reject_node", reject_node)
    graph.set_entry_point("seed")
    graph.add_edge("seed", "human_approval")
    graph.add_conditional_edges(
        "human_approval",
        _route,
        {"end_node": "end_node", "reject_node": "reject_node"},
    )

    compiled = graph.compile(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "t-changes"}}

    await compiled.ainvoke({"metadata": {}}, config)
    final = await compiled.ainvoke(
        Command(
            resume={
                "action": "request_changes",
                "modifications": {"revision_note": "please add a unit test"},
            },
        ),
        config,
    )

    assert final["metadata"]["reached"] == "rejected"
    assert final["metadata"]["saw_revision"] == "please add a unit test"


@pytest.mark.asyncio
async def test_bypass_when_hitl_disabled_at_compile() -> None:
    """When HITL is disabled at the gate level, the workflow never pauses."""

    async def end_node(state: _MiniState) -> dict[str, Any]:
        return {"metadata": {"reached": "approved"}}

    async def reject_node(state: _MiniState) -> dict[str, Any]:
        return {"metadata": {"reached": "rejected"}}

    seed = seed_hitl_metadata("review_result", "HIGH")
    gate = hitl_gate(
        artifact_field="review_result",
        on_approve="end_node",
        on_reject="reject_node",
        bypass_condition=lambda _s: True,  # always bypass
    )

    def _route(state: _MiniState) -> str:
        return gate(state)

    graph: StateGraph[_MiniState, Any, Any, Any] = StateGraph(_MiniState)
    graph.add_node("seed", seed)
    # Bypass means human_approval_node is wired but never reached via gate;
    # we still register it so the compile is identical to the production graph.
    graph.add_node("human_approval", human_approval_node)
    graph.add_node("end_node", end_node)
    graph.add_node("reject_node", reject_node)
    graph.set_entry_point("seed")
    graph.add_edge("seed", "human_approval")
    graph.add_conditional_edges(
        "human_approval",
        _route,
        {"end_node": "end_node", "reject_node": "reject_node"},
    )

    compiled = graph.compile(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "t-bypass"}}

    # The workflow still pauses at human_approval because the bypass only
    # affects the routing function (which runs *after* the node).  But on
    # resume the bypass forces approval regardless of the human's choice.
    await compiled.ainvoke({"metadata": {}}, config)
    final = await compiled.ainvoke(
        Command(resume={"action": "reject", "modifications": {}}),
        config,
    )
    # Even though the human said "reject", the bypass routes to on_approve.
    assert final["metadata"]["reached"] == "approved"
