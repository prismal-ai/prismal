"""End-to-end Skynet swarm demo with injected fakes (Fase S).

Builds the skynet subgraph (plan → Send fan-out → worker ⇉ reduce → evaluate
→ output) with deterministic planner/worker/evaluator/reducer backends — no
LLM, no network — and runs a parallelizable order through a 3-worker swarm.

Run::

    python examples/skynet_swarm.py
"""

from __future__ import annotations

import asyncio

from langchain_core.messages import HumanMessage

from prismal.agents.skynet.types import SwarmOrder, SwarmPlan, WorkerResult
from prismal.agents.subgraphs.factory import assemble_state_graph
from prismal.agents.subgraphs.skynet import build_skynet_subgraph

GOAL = "Research competitors Acme, Globex and Initech in parallel"

_COMPETITORS = ["Acme", "Globex", "Initech"]


async def fake_plan(messages: list[dict[str, str]]) -> SwarmPlan:
    """Deterministic stand-in for the planner LLM call.

    A real deployment omits ``plan_fn`` so the supervisor lazily wires
    ``ProviderRegistry().get_llm()`` (honouring ``skynet_planner_model``) and
    the model chooses the swarm size from the goal.
    """
    return SwarmPlan(
        goal="",
        orders=[
            SwarmOrder(order_id=f"ord-{i}", instruction=f"Research competitor {name}")
            for i, name in enumerate(_COMPETITORS, start=1)
        ],
        rationale="one worker per competitor",
    )


async def fake_worker(messages: list[dict[str, str]]) -> str:
    """Deterministic stand-in for the per-order worker LLM call."""
    user = messages[1]["content"]
    name = next((n for n in _COMPETITORS if n in user), "unknown")
    return f"{name}: strong in EU, weak mobile offering, pricing mid-market."


async def fake_evaluate(messages: list[dict[str, str]]) -> tuple[bool, str]:
    """Deterministic stand-in for the evaluator LLM call."""
    return (
        True,
        "All three competitors researched: each is strong regionally but "
        "under-invested in mobile — a clear differentiation opportunity.",
    )


async def fake_reduce(goal: str, results: list[WorkerResult]) -> str:
    """Deterministic stand-in for the synthesis LLM call."""
    bullet_list = "\n".join(f"- {r.output}" for r in results)
    return f"Competitive landscape:\n{bullet_list}"


async def main() -> None:
    """Build the subgraph with fakes, run one swarm, print the SwarmResult."""
    definition = build_skynet_subgraph(
        plan_fn=fake_plan,
        worker_fn=fake_worker,
        evaluate_fn=fake_evaluate,
        reduce_fn=fake_reduce,
    )
    graph = assemble_state_graph(definition).compile()

    result = await graph.ainvoke({"messages": [HumanMessage(content=GOAL)]})

    swarm_result = result["metadata"]["skynet"]["result"]
    print(f"Goal: {GOAL}\n")
    print(f"Swarm size: {len(swarm_result.worker_results)} workers")
    print(f"Rounds: {swarm_result.rounds_completed}  Complete: {swarm_result.complete}\n")
    for worker_result in swarm_result.worker_results:
        status = "ok" if worker_result.success else f"FAILED ({worker_result.error})"
        print(f"  [{worker_result.order_id} / {status}] {worker_result.output}")
    print(f"\nFinal answer:\n{result['messages'][-1].content}")


if __name__ == "__main__":
    asyncio.run(main())
