"""Tool calls made inside an agent's ReAct loop are visible to assertions.

Built-in agents run their tools inside ``react_loop`` and return only the
final answer, so the ``updates`` stream carries no tool step: ``must_call``
could never pass on a live run. ``capture_trajectory``'s callback also sees
every tool call of the run (as it does LLM calls) and records the names.
"""

from __future__ import annotations

import contextlib
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from prismal.eval.assertions import assert_tool_usage
from prismal.eval.trajectory import capture_trajectory
from prismal.eval.types import Assertion, AssertionType, EvalCase
from prismal.langgraph import END, START, AgentState, StateGraph


@tool
def get_order(order_id: str) -> str:
    """Return an order record."""
    return f"record {order_id}"


@tool
def broken(order_id: str) -> str:
    """Always fails."""
    raise ValueError("nope")


async def _agent(_state: AgentState) -> dict[str, Any]:
    # Like react_loop: tools run inside the node, config is not forwarded.
    await get_order.ainvoke({"order_id": "ORD-001"})
    await get_order.ainvoke({"order_id": "ORD-002"})
    with contextlib.suppress(ValueError):
        await broken.ainvoke({"order_id": "x"})
    return {"messages": [AIMessage(content="total 42")]}


def _graph() -> Any:
    builder = StateGraph(AgentState)
    builder.add_node("researcher", _agent)
    builder.add_edge(START, "researcher")
    builder.add_edge("researcher", END)
    return builder.compile()


def _case() -> EvalCase:
    must = Assertion(type=AssertionType.TOOL_USAGE, must_call=["get_order"], max_steps=4)
    return EvalCase(id="c", input="sum", assertions=[must])


async def test_internal_tool_calls_are_recorded() -> None:
    traj = await capture_trajectory(_graph(), _case())
    assert traj.observed_tool_names == ["get_order", "get_order", "broken"]
    assert (traj.tool_calls, traj.tool_errors) == (3, 1)
    assert len(traj.steps) == 1  # steps (and max_steps) keep their meaning


async def test_must_call_sees_internal_tool_calls() -> None:
    case = _case()
    traj = await capture_trajectory(_graph(), case)
    assert assert_tool_usage(traj, case.assertions[0]).passed
