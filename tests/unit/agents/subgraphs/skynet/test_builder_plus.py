"""Unit tests for the Skynet S+ builder wiring (SPEC-SP-INT-001).

The builder constructs ONE shared ``CostMeter`` and threads it into the
supervisor, every worker, the reducer, and the output node so ``SwarmResult.usage``
is the whole-swarm total.  It also accepts ``role_registry`` / ``send_fn`` /
``budget_guard_fn``.  All backends faked — no LLM, no network.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import HumanMessage

from prismal.agents.skynet.roles import RoleRegistry, SpecialistRole
from prismal.agents.skynet.types import SwarmOrder, SwarmPlan, SwarmResult
from prismal.agents.subgraphs.factory import assemble_state_graph
from prismal.agents.subgraphs.skynet import build_skynet_subgraph
from prismal.core.config import Settings


class _FakeResponse:
    def __init__(self, content: str, usage_metadata: dict[str, int] | None = None) -> None:
        self.content = content
        self.usage_metadata = usage_metadata


class MeteringProviderRegistry:
    """ProviderRegistry stand-in whose LLM returns a fixed usage_metadata."""

    def __init__(self, *, settings: Any = None) -> None:
        self._settings = settings

    def get_llm(self, *, model: str | None = None) -> Any:
        class _LLM:
            async def ainvoke(self, _messages: list[Any]) -> _FakeResponse:
                return _FakeResponse(
                    "worker output", usage_metadata={"input_tokens": 30, "output_tokens": 12}
                )

        return _LLM()


class StubAudit:
    def log_event(self, event_type: str, payload: dict[str, object]) -> None:
        return None


async def _run(definition: Any, goal: str = "do A and B") -> dict[str, Any]:
    graph = assemble_state_graph(definition).compile()
    return await graph.ainvoke({"messages": [HumanMessage(content=goal)]})


def test_builder_shares_single_meter(monkeypatch: pytest.MonkeyPatch) -> None:
    """One CostMeter is threaded into supervisor, worker, reducer, and output."""
    seen: dict[str, Any] = {}

    real_supervisor = __import__(
        "prismal.agents.skynet.supervisor", fromlist=["SkynetSupervisor"]
    ).SkynetSupervisor
    real_worker = __import__("prismal.agents.skynet.worker", fromlist=["SwarmWorker"]).SwarmWorker

    def spy_supervisor(**kwargs: Any) -> Any:
        seen["supervisor"] = kwargs.get("meter")
        return real_supervisor(**kwargs)

    def spy_worker(**kwargs: Any) -> Any:
        seen["worker"] = kwargs.get("meter")
        return real_worker(**kwargs)

    monkeypatch.setattr("prismal.agents.subgraphs.skynet.builder.SkynetSupervisor", spy_supervisor)
    monkeypatch.setattr("prismal.agents.subgraphs.skynet.builder.SwarmWorker", spy_worker)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    build_skynet_subgraph(settings=settings)

    assert seen["supervisor"] is not None
    assert seen["supervisor"] is seen["worker"]


async def test_swarm_result_usage_is_whole_swarm(monkeypatch: pytest.MonkeyPatch) -> None:
    """SwarmResult.usage aggregates the shared meter (Σ metered workers)."""
    monkeypatch.setattr("prismal.providers.registry.ProviderRegistry", MeteringProviderRegistry)

    async def plan_fn(_messages: list[dict[str, str]]) -> SwarmPlan:
        return SwarmPlan(
            goal="",
            orders=[
                SwarmOrder(order_id="ord-1", instruction="a"),
                SwarmOrder(order_id="ord-2", instruction="b"),
            ],
            rationale="split",
        )

    async def evaluate_fn(_messages: list[dict[str, str]]) -> tuple[bool, str]:
        return True, "done"

    async def reduce_fn(_goal: str, results: list[Any]) -> str:
        return f"merged {len(results)}"

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    definition = build_skynet_subgraph(
        settings=settings,
        plan_fn=plan_fn,
        evaluate_fn=evaluate_fn,
        reduce_fn=reduce_fn,
        audit=StubAudit(),  # type: ignore[arg-type]
    )

    result = await _run(definition)
    swarm_result: SwarmResult = result["metadata"]["skynet"]["result"]

    # Two workers × 42 tokens each recorded into the single shared meter.
    assert swarm_result.usage.total_tokens == 84
    assert swarm_result.usage.calls == 2


async def test_e2e_specialist_metered_remote_with_fakes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A specialist + metered local worker and a faked remote worker both run."""
    monkeypatch.setattr("prismal.providers.registry.ProviderRegistry", MeteringProviderRegistry)

    registry = RoleRegistry(
        {
            "researcher": SpecialistRole(name="researcher", model="model-r"),
            "legal": SpecialistRole(
                name="legal",
                remote_agent="https://legal.example.com/.well-known/agent-card.json",
            ),
        }
    )

    async def plan_fn(_messages: list[dict[str, str]]) -> SwarmPlan:
        return SwarmPlan(
            goal="",
            orders=[
                SwarmOrder(order_id="ord-1", instruction="research", role="researcher"),
                SwarmOrder(order_id="ord-2", instruction="review", role="legal"),
            ],
            rationale="split",
        )

    async def evaluate_fn(_messages: list[dict[str, str]]) -> tuple[bool, str]:
        return True, "done"

    async def reduce_fn(_goal: str, results: list[Any]) -> str:
        return f"merged {len(results)}"

    remote_calls: list[str] = []

    async def send_fn(role: Any, order: Any) -> str:
        remote_calls.append(role.name)
        return "remote legal answer"

    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        skynet_specialists_enabled=True,
        skynet_remote_workers_enabled=True,
        a2a_enabled=True,
    )
    definition = build_skynet_subgraph(
        settings=settings,
        plan_fn=plan_fn,
        evaluate_fn=evaluate_fn,
        reduce_fn=reduce_fn,
        role_registry=registry,
        send_fn=send_fn,
        audit=StubAudit(),  # type: ignore[arg-type]
    )

    result = await _run(definition)
    swarm_result: SwarmResult = result["metadata"]["skynet"]["result"]

    by_id = {r.order_id: r for r in swarm_result.worker_results}
    assert by_id["ord-1"].success is True
    assert by_id["ord-1"].remote is False  # local specialist, metered
    assert by_id["ord-2"].remote is True  # remote worker
    assert by_id["ord-2"].output == "remote legal answer"
    assert remote_calls == ["legal"]
    # Only the local specialist worker was metered (remote has no token metadata).
    assert swarm_result.usage.total_tokens == 42
