"""Skynet S+ demo: specialist roles + metering + a faked remote worker (Fase S+).

Builds the skynet subgraph with a :class:`RoleRegistry` binding two roles — a
local ``researcher`` specialist (its own model/persona/tools) and a ``legal``
role bound to a remote A2A agent — plus a shared ``CostMeter`` so the whole
swarm's token usage lands on ``SwarmResult.usage``.  Everything is faked (a
metering LLM stand-in + an in-process ``send_fn``) so it runs with no LLM and no
network.

Run::

    python examples/skynet_specialist_swarm.py
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.messages import HumanMessage

from prismal.agents.skynet.roles import RoleRegistry, SpecialistRole
from prismal.agents.skynet.types import SwarmOrder, SwarmPlan, WorkerResult
from prismal.agents.subgraphs.factory import assemble_state_graph
from prismal.agents.subgraphs.skynet import build_skynet_subgraph
from prismal.core.config import Settings

GOAL = "Draft a launch plan: research the market and get a legal sign-off"


class _FakeResponse:
    """Minimal LLM response carrying LangChain-style ``usage_metadata``."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.usage_metadata = {"input_tokens": 40, "output_tokens": 15}


class MeteringProviderRegistry:
    """A metering ``ProviderRegistry`` stand-in (no real provider, no network)."""

    def __init__(self, *, settings: Any = None) -> None:
        self._settings = settings

    def get_llm(self, *, model: str | None = None) -> Any:
        class _LLM:
            async def ainvoke(self, _messages: list[Any]) -> _FakeResponse:
                return _FakeResponse(f"[{model or 'default'}] market looks favorable")

        return _LLM()


async def fake_plan(_messages: list[dict[str, str]]) -> SwarmPlan:
    """Planner stand-in tagging each sub-order with a specialist role."""
    return SwarmPlan(
        goal="",
        orders=[
            SwarmOrder(order_id="ord-1", instruction="Research the market", role="researcher"),
            SwarmOrder(order_id="ord-2", instruction="Review the plan legally", role="legal"),
        ],
        rationale="one specialist per sub-order",
    )


async def fake_evaluate(_messages: list[dict[str, str]]) -> tuple[bool, str]:
    """Evaluator stand-in — the goal is met after one round."""
    return True, "Market researched and legally cleared."


async def fake_reduce(_goal: str, results: list[WorkerResult]) -> str:
    """Synthesis stand-in."""
    return "Launch plan:\n" + "\n".join(f"- {r.output}" for r in results)


async def fake_send(role: Any, order: SwarmOrder) -> str:
    """Remote A2A delegation stand-in for the ``legal`` role (no network)."""
    return f"[remote {role.name}] approved: {order.instruction}"


def _registry() -> RoleRegistry:
    return RoleRegistry(
        {
            "researcher": SpecialistRole(
                name="researcher",
                model="research-model",
                capabilities=["research", "web"],
                persona="You are a meticulous market analyst.",
            ),
            "legal": SpecialistRole(
                name="legal",
                remote_agent="https://legal.example.com/.well-known/agent-card.json",
            ),
        }
    )


async def main() -> None:
    """Build the S+ subgraph with fakes, run one swarm, print the SwarmResult."""
    # Enable specialists + remote workers; the metering provider stands in for
    # the local worker's LLM. A real deployment omits these fakes.
    import prismal.providers.registry as registry_module

    registry_module.ProviderRegistry = MeteringProviderRegistry  # type: ignore[assignment,misc]

    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        skynet_specialists_enabled=True,
        skynet_remote_workers_enabled=True,
        a2a_enabled=True,
    )
    definition = build_skynet_subgraph(
        settings=settings,
        plan_fn=fake_plan,
        evaluate_fn=fake_evaluate,
        reduce_fn=fake_reduce,
        role_registry=_registry(),
        send_fn=fake_send,
    )
    graph = assemble_state_graph(definition).compile()

    result = await graph.ainvoke({"messages": [HumanMessage(content=GOAL)]})

    swarm_result = result["metadata"]["skynet"]["result"]
    print(f"Goal: {GOAL}\n")
    for worker_result in swarm_result.worker_results:
        where = "remote" if worker_result.remote else "local"
        print(
            f"  [{worker_result.order_id} / {worker_result.role} / {where}] {worker_result.output}"
        )
    print(
        f"\nWhole-swarm usage: {swarm_result.usage.total_tokens} tokens "
        f"across {swarm_result.usage.calls} metered call(s)"
    )
    print(f"\nFinal answer:\n{result['messages'][-1].content}")


if __name__ == "__main__":
    asyncio.run(main())
