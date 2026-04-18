"""End-to-end HITL workflow integration test (Phase 35 / SPEC-037).

Verifies the full pause → resume cycle for ``human_approval_node`` against a
real LangGraph compiled graph with an in-memory checkpointer.  The
production dev_pipeline subgraph is too expensive to spin up here (it pulls
LLM providers, MCP servers, and 11 nodes), so this integration test
focuses on the gate wiring itself with a minimal 4-node graph that
mirrors the dev_pipeline's HITL topology:

    seed → human_approval → [hitl_gate] → end_node | reject_node
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph
from langgraph.types import Command

from lightagent.agents.subgraphs.gates import (
    hitl_gate,
    human_approval_node,
    seed_hitl_metadata,
)


class _MiniState(TypedDict):
    metadata: Annotated[dict[str, Any], lambda a, b: {**a, **b}]


def _build_graph() -> Any:
    """Build the minimal HITL graph used by the integration tests."""

    async def end_node(state: _MiniState) -> dict[str, Any]:
        return {"metadata": {"reached": "approved", "pipeline_done": True}}

    async def reject_node(state: _MiniState) -> dict[str, Any]:
        return {
            "metadata": {
                "reached": "rejected",
                "pipeline_done": True,
                "saw_revision": state["metadata"].get("revision_note", ""),
            },
        }

    seed = seed_hitl_metadata("dev_pipeline.code_artifact", "HIGH")
    gate = hitl_gate(
        artifact_field="dev_pipeline.code_artifact",
        on_approve="end_node",
        on_reject="reject_node",
        risk_level="HIGH",
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
    return graph.compile(checkpointer=MemorySaver())


@pytest.mark.asyncio
async def test_hitl_full_pause_then_approve_completes_to_end() -> None:
    """Full lifecycle: invoke → pause at interrupt → resume approve → END."""
    compiled = _build_graph()
    config = {"configurable": {"thread_id": "integration-approve"}}
    initial = {
        "metadata": {
            "dev_pipeline": {
                "code_artifact": {
                    "file_path": "src/payment.py",
                    "content": "def charge(...): ...",
                },
            },
        },
    }

    # First turn: workflow runs through seed + human_approval and pauses.
    result = await compiled.ainvoke(initial, config)
    snapshot = await compiled.aget_state(config)

    assert snapshot.next, "Workflow should be suspended at the human_approval interrupt"
    assert "reached" not in result.get("metadata", {})

    # The interrupt payload is exposed in snapshot.tasks[i].interrupts[0].value.
    # We verify it carries the artifact field + risk level so the dashboard
    # can render the approval card.
    payload_seen = False
    for task in snapshot.tasks:
        for interrupt in getattr(task, "interrupts", ()):
            value = getattr(interrupt, "value", None)
            if isinstance(value, dict) and value.get("risk_level") == "HIGH":
                payload_seen = True
                assert value.get("artifact_field") == "dev_pipeline.code_artifact"
                assert "options" in value
    assert payload_seen, "Interrupt payload should expose risk_level + options"

    # Second turn: human approves.  Workflow resumes and completes.
    final = await compiled.ainvoke(
        Command(resume={"action": "approve", "modifications": {}}),
        config,
    )
    assert final["metadata"]["reached"] == "approved"
    assert final["metadata"]["pipeline_done"] is True


@pytest.mark.asyncio
async def test_hitl_full_pause_then_reject_loops_back() -> None:
    """Reject path routes the workflow to the on_reject node."""
    compiled = _build_graph()
    config = {"configurable": {"thread_id": "integration-reject"}}

    await compiled.ainvoke({"metadata": {}}, config)
    final = await compiled.ainvoke(
        Command(resume={"action": "reject", "modifications": {}}),
        config,
    )
    assert final["metadata"]["reached"] == "rejected"


@pytest.mark.asyncio
async def test_hitl_request_changes_merges_modifications_into_state() -> None:
    """request_changes propagates the human revision note into metadata."""
    compiled = _build_graph()
    config = {"configurable": {"thread_id": "integration-changes"}}

    await compiled.ainvoke({"metadata": {}}, config)
    final = await compiled.ainvoke(
        Command(
            resume={
                "action": "request_changes",
                "modifications": {
                    "revision_note": "rename the function to charge_card",
                },
            },
        ),
        config,
    )
    assert final["metadata"]["reached"] == "rejected"
    assert final["metadata"]["saw_revision"] == ("rename the function to charge_card")
