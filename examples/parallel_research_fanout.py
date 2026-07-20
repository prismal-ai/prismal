"""Parallel research fan-out / fan-in demo with a deterministic worker.

Rebuilds the supervisor's Phase 34 map-reduce branch as a small standalone
``StateGraph`` (imports via :mod:`prismal.langgraph`), reusing the real
anchor and aggregator nodes but swapping the LLM-backed worker for a
deterministic fake — no provider keys, no network.

Topology (mirrors ``prismal/agents/graph.py``)::

    START -> parallel_researcher ──[dispatcher: one Send per pending task]──┐
                                                                            │
                        ┌───────────────────────────────────────────────────┘
                        ▼
              fake_research_worker   (N concurrent invocations)
                        │  each appends one dict to state["parallel_results"]
                        ▼            (operator.add reducer merges the fan-in)
              research_aggregator    (real node: builds the markdown summary)
                        │
                        ▼
                       END

The dispatcher comes from ``make_parallel_dispatcher`` — the same factory
that builds ``research_dispatcher`` in production — pointed at the fake
worker.  With an empty ``pending_tasks`` list it routes straight to the
aggregator (its ``on_empty`` target), which is also demonstrated.

Run::

    python examples/parallel_research_fanout.py
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.messages import HumanMessage

from prismal.agents.parallel_research import (
    parallel_researcher_node,
    research_aggregator_node,
)
from prismal.agents.patterns.parallel import make_parallel_dispatcher
from prismal.langgraph import END, START, AgentState, StateGraph, create_initial_state

TASKS = [
    "Compare vector databases for on-prem RAG",
    "Survey chunking strategies for long PDFs",
    "List evaluation metrics for retrieval quality",
]


async def fake_research_worker(state: dict[str, Any]) -> dict[str, Any]:
    """Deterministic stand-in for ``parallel_researcher_worker``.

    The dispatcher copies the parent state into each ``Send`` payload and
    puts the assigned sub-task under ``state["_task"]``.  A real worker runs
    a bounded ReAct loop with the researcher tools; this fake just derives
    a canned finding so the fan-out/fan-in mechanics run offline.
    """
    task = str(state.get("_task", ""))
    return {
        "parallel_results": [
            {
                "agent": "fake_research_worker",
                "task": task,
                "result": f"Deterministic findings for: {task.lower()}",
            }
        ]
    }


def build_fanout_graph() -> Any:
    """Wire dispatcher -> workers -> aggregator into a compiled graph."""
    dispatcher = make_parallel_dispatcher(
        tasks_field="pending_tasks",
        worker_node="fake_research_worker",
        max_workers=5,
        on_empty="research_aggregator",
    )

    builder = StateGraph(AgentState)
    builder.add_node("parallel_researcher", parallel_researcher_node)
    builder.add_node("fake_research_worker", fake_research_worker)
    builder.add_node("research_aggregator", research_aggregator_node)

    builder.add_edge(START, "parallel_researcher")
    builder.add_conditional_edges(
        "parallel_researcher",
        dispatcher,
        {
            "fake_research_worker": "fake_research_worker",
            "research_aggregator": "research_aggregator",
        },
    )
    builder.add_edge("fake_research_worker", "research_aggregator")
    builder.add_edge("research_aggregator", END)
    return builder.compile()


async def main() -> None:
    """Fan three research sub-tasks out, aggregate, then show the empty path."""
    graph = build_fanout_graph()

    state = create_initial_state(session_id="fanout-demo")
    state["messages"] = [HumanMessage(content="Research the RAG stack options.")]
    state["pending_tasks"] = list(TASKS)

    result = await graph.ainvoke(state)

    print(f"Dispatched {len(TASKS)} sub-tasks in parallel.\n")
    print("Merged parallel_results (operator.add reducer):")
    for item in result["parallel_results"]:
        print(f"  - [{item['agent']}] {item['task']!r}")
    print("\nAggregated summary (last AIMessage):\n")
    print(result["messages"][-1].content)

    # Empty task list -> the dispatcher routes straight to the aggregator.
    empty_state = create_initial_state(session_id="fanout-empty")
    empty_state["messages"] = [HumanMessage(content="Nothing to fan out.")]
    empty_result = await graph.ainvoke(empty_state)
    print("\nWith no pending_tasks the dispatcher takes the on_empty route:")
    print(f"  {empty_result['messages'][-1].content}")


if __name__ == "__main__":
    asyncio.run(main())
