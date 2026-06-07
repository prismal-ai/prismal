"""Unit tests for the skynet subgraph (SPEC-SKY-SG-001).

Covers RF-SKY-04/06/07/08/12 and the S4 "done when" criteria: the subgraph
runs end-to-end with injected fakes and no provider import (AST guard in
``tests/unit/agents/skynet/test_no_provider_imports.py``); fan-out respects
the cap; the control loop terminates.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage

from prismal.agents.skynet.supervisor import SkynetSupervisor
from prismal.agents.skynet.types import SwarmOrder, SwarmPlan, SwarmResult
from prismal.agents.skynet.worker import SwarmWorker
from prismal.agents.subgraphs.factory import assemble_state_graph
from prismal.agents.subgraphs.registry import SubgraphDefinition, SubgraphRegistry
from prismal.agents.subgraphs.skynet import build_skynet_subgraph, register_skynet
from prismal.core.config import Settings

# ── fixtures / fakes ─────────────────────────────────────────────────────────


def _orders(n: int) -> list[SwarmOrder]:
    return [SwarmOrder(order_id=f"ord-{i}", instruction=f"subtask {i}") for i in range(n)]


class FakePlanner:
    """plan_fn fake counting calls."""

    def __init__(self, orders: list[SwarmOrder]) -> None:
        self.orders = orders
        self.calls = 0

    async def __call__(self, messages: list[dict[str, str]]) -> SwarmPlan:
        self.calls += 1
        return SwarmPlan(goal="", orders=self.orders, rationale="split")


class FakeEvaluator:
    """evaluate_fn fake replaying a scripted sequence of verdicts."""

    def __init__(self, *verdicts: tuple[bool, str]) -> None:
        self.verdicts = list(verdicts) or [(True, "done")]
        self.calls = 0

    async def __call__(self, messages: list[dict[str, str]]) -> tuple[bool, str]:
        self.calls += 1
        verdict = self.verdicts.pop(0) if len(self.verdicts) > 1 else self.verdicts[0]
        return verdict


class FakeWorkerFn:
    """worker_fn fake echoing per-order output; can fail on first attempts."""

    def __init__(self, fail_first_for: str | None = None) -> None:
        self.fail_first_for = fail_first_for
        self.seen: list[str] = []

    async def __call__(self, messages: list[dict[str, str]]) -> str:
        user = messages[1]["content"]
        self.seen.append(user)
        if self.fail_first_for and self.fail_first_for in user:
            self.fail_first_for = None  # fail only the first time
            raise RuntimeError("worker exploded")
        return "worker output"


class StubAudit:
    """No-op audit logger (avoids JSONL file writes in tests)."""

    def log_event(self, event_type: str, payload: dict[str, object]) -> None:
        return None


async def _fake_reduce(goal: str, results: list[Any]) -> str:
    return f"reduced {len(results)} results"


def _build(
    planner: FakePlanner,
    evaluator: FakeEvaluator,
    worker_fn: FakeWorkerFn | None = None,
    **settings_kwargs: Any,
) -> SubgraphDefinition:
    settings = Settings(**settings_kwargs)
    supervisor = SkynetSupervisor(
        plan_fn=planner,
        evaluate_fn=evaluator,
        audit=StubAudit(),  # type: ignore[arg-type]
        settings=settings,
    )
    worker = SwarmWorker(worker_fn=worker_fn or FakeWorkerFn(), settings=settings)
    return build_skynet_subgraph(
        settings=settings,
        supervisor=supervisor,
        worker=worker,
        reduce_fn=_fake_reduce,
    )


async def _run(definition: SubgraphDefinition, goal: str = "do A, B and C") -> dict[str, Any]:
    graph = assemble_state_graph(definition).compile()
    return await graph.ainvoke({"messages": [HumanMessage(content=goal)]})


# ── topology (S4-02/03/04/05) ────────────────────────────────────────────────


def test_subgraph_topology() -> None:
    """5 nodes, plan entry, Send fan-out from plan, re-plan edge from evaluate."""
    definition = _build(FakePlanner(_orders(2)), FakeEvaluator())
    assert definition.name == "skynet"
    assert definition.entry_point == "skynet_plan"
    assert list(definition.nodes) == [
        "skynet_plan",
        "skynet_worker",
        "skynet_reduce",
        "skynet_evaluate",
        "skynet_output",
    ]
    assert ("skynet_worker", "skynet_reduce") in definition.edges
    assert ("skynet_reduce", "skynet_evaluate") in definition.edges
    assert [src for src, _ in definition.send_edges] == ["skynet_plan"]
    assert list(definition.conditional_edges) == ["skynet_evaluate"]


# ── end-to-end with fakes (RF-SKY-11) ────────────────────────────────────────


async def test_end_to_end_single_round() -> None:
    """plan → fan-out → reduce → evaluate(complete) → output."""
    result = await _run(_build(FakePlanner(_orders(3)), FakeEvaluator((True, "all done"))))

    final = str(result["messages"][-1].content)
    assert "all done" in final

    skynet = result["metadata"]["skynet"]
    assert skynet["goal"] == "do A, B and C"
    assert skynet["complete"] is True
    swarm_result = skynet["result"]
    assert isinstance(swarm_result, SwarmResult)
    assert swarm_result.complete is True
    assert swarm_result.rounds_completed == 1
    assert len(swarm_result.worker_results) == 3
    assert all(r.success for r in swarm_result.worker_results)


async def test_fanout_dispatches_one_worker_per_order() -> None:
    """RF-SKY-04: exactly plan.size workers run (visible in parallel_results)."""
    result = await _run(_build(FakePlanner(_orders(4)), FakeEvaluator()))
    tagged = [
        e
        for e in result["parallel_results"]
        if isinstance(e, dict) and e.get("agent") == "skynet_worker"
    ]
    assert len(tagged) == 4


async def test_fanout_respects_swarm_cap_and_defers_overflow() -> None:
    """RF-SKY-03: 5 orders with cap 3 → 3 workers run, 2 deferred in result."""
    definition = _build(
        FakePlanner(_orders(5)),
        FakeEvaluator((True, "enough")),
        skynet_max_swarm=3,
    )
    result = await _run(definition)
    swarm_result = result["metadata"]["skynet"]["result"]
    assert len(swarm_result.worker_results) == 3
    assert len(swarm_result.deferred_orders) == 2


# ── control loop (RF-SKY-07/08) ──────────────────────────────────────────────


async def test_failed_orders_replanned_deterministically() -> None:
    """A failed worker's order is re-dispatched next round without re-planning."""
    planner = FakePlanner(_orders(2))
    worker_fn = FakeWorkerFn(fail_first_for="subtask 1")
    evaluator = FakeEvaluator((False, "missing piece"), (True, "now complete"))
    result = await _run(_build(planner, evaluator, worker_fn, skynet_max_rounds=3))

    skynet = result["metadata"]["skynet"]
    swarm_result = skynet["result"]
    assert planner.calls == 1  # round 2 was a deterministic pass-through
    assert swarm_result.rounds_completed == 2
    assert swarm_result.complete is True
    # the failed order was retried and now succeeds (dedupe keeps the latest)
    by_id = {r.order_id: r for r in swarm_result.worker_results}
    assert by_id["ord-1"].success is True
    assert len(by_id) == 2


async def test_deferred_orders_resume_next_round() -> None:
    """Deferred overflow runs in the following round (never dropped)."""
    planner = FakePlanner(_orders(5))
    evaluator = FakeEvaluator((False, "keep going"), (True, "complete"))
    result = await _run(_build(planner, evaluator, skynet_max_swarm=3, skynet_max_rounds=3))

    swarm_result = result["metadata"]["skynet"]["result"]
    assert swarm_result.rounds_completed == 2
    assert len(swarm_result.worker_results) == 5  # 3 in round 1 + 2 deferred in round 2
    assert swarm_result.deferred_orders == []


async def test_loop_never_exceeds_max_rounds() -> None:
    """RF-SKY-08: an always-incomplete evaluation stops at skynet_max_rounds."""
    worker_fn = FakeWorkerFn()
    evaluator = FakeEvaluator((False, "never satisfied"))

    # one order always fails → there is always something to re-plan
    class AlwaysFail:
        async def __call__(self, messages: list[dict[str, str]]) -> str:
            raise RuntimeError("always broken")

    settings = Settings(skynet_max_rounds=2)
    supervisor = SkynetSupervisor(
        plan_fn=FakePlanner(_orders(1)),
        evaluate_fn=evaluator,
        audit=StubAudit(),  # type: ignore[arg-type]
        settings=settings,
    )
    definition = build_skynet_subgraph(
        settings=settings,
        supervisor=supervisor,
        worker=SwarmWorker(worker_fn=AlwaysFail(), settings=settings),
        reduce_fn=_fake_reduce,
    )
    result = await _run(definition)
    swarm_result = result["metadata"]["skynet"]["result"]
    assert swarm_result.rounds_completed == 2
    assert swarm_result.complete is False
    assert evaluator.calls == 2
    del worker_fn


# ── error paths ──────────────────────────────────────────────────────────────


async def test_plan_failure_terminates_with_error_message() -> None:
    """A failing planner yields an error message; no worker runs."""

    class BoomPlanner:
        async def __call__(self, messages: list[dict[str, str]]) -> SwarmPlan:
            raise RuntimeError("planner down")

    settings = Settings()
    supervisor = SkynetSupervisor(
        plan_fn=BoomPlanner(),
        evaluate_fn=FakeEvaluator(),
        audit=StubAudit(),  # type: ignore[arg-type]
        settings=settings,
    )
    definition = build_skynet_subgraph(
        settings=settings,
        supervisor=supervisor,
        worker=SwarmWorker(worker_fn=FakeWorkerFn(), settings=settings),
        reduce_fn=_fake_reduce,
    )
    result = await _run(definition)
    assert "Skynet could not plan" in str(result["messages"][-1].content)
    assert "planner down" in result["metadata"]["skynet"]["error"]
    assert result.get("parallel_results", []) == []


async def test_evaluator_failure_stops_the_loop_gracefully() -> None:
    """A broken evaluator must not spin the swarm: the run terminates degraded."""

    class BoomEvaluator:
        async def __call__(self, messages: list[dict[str, str]]) -> tuple[bool, str]:
            raise RuntimeError("evaluator down")

    settings = Settings(skynet_max_rounds=3)
    supervisor = SkynetSupervisor(
        plan_fn=FakePlanner(_orders(2)),
        evaluate_fn=BoomEvaluator(),
        audit=StubAudit(),  # type: ignore[arg-type]
        settings=settings,
    )
    definition = build_skynet_subgraph(
        settings=settings,
        supervisor=supervisor,
        worker=SwarmWorker(worker_fn=FakeWorkerFn(), settings=settings),
        reduce_fn=_fake_reduce,
    )
    result = await _run(definition)
    skynet = result["metadata"]["skynet"]
    assert "evaluator down" in skynet["error"]
    swarm_result = skynet["result"]
    assert swarm_result.complete is False
    assert swarm_result.rounds_completed == 1  # single round, no spin
    # the reducer's answer survives the degraded evaluation
    assert "reduced 2 results" in str(result["messages"][-1].content)


async def test_empty_goal_terminates_with_error_message() -> None:
    """An empty goal never reaches the planner."""
    planner = FakePlanner(_orders(2))
    result = await _run(_build(planner, FakeEvaluator()), goal="")
    assert "Skynet could not plan" in str(result["messages"][-1].content)
    assert planner.calls == 0


# ── state namespacing (RF-SKY-12 / S4-06) ────────────────────────────────────


async def test_state_is_namespaced_under_metadata_skynet() -> None:
    """Durable Skynet state lives below metadata.skynet only."""
    result = await _run(_build(FakePlanner(_orders(2)), FakeEvaluator()))
    assert set(result["metadata"]) == {"skynet"}
    skynet = result["metadata"]["skynet"]
    assert {"goal", "round", "orders", "results", "answer", "complete", "result"} <= set(skynet)


async def test_dispatch_channel_is_cleared_after_output() -> None:
    """The working skynet_orders channel is drained when the run terminates."""
    result = await _run(_build(FakePlanner(_orders(2)), FakeEvaluator()))
    assert result.get("skynet_orders", []) == []


# ── dispatcher fan-out (S6-04 / RF-SKY-04) ───────────────────────────────────


def _order_dicts(n: int) -> list[dict[str, Any]]:
    return [
        {
            "order_id": f"ord-{i}",
            "instruction": f"t{i}",
            "role": "worker",
            "context": {},
            "attempt": 1,
        }
        for i in range(n)
    ]


def _get_dispatcher(definition: SubgraphDefinition) -> Any:
    [(source, dispatcher)] = definition.send_edges
    assert source == "skynet_plan"
    return dispatcher


def test_dispatcher_emits_one_send_per_staged_order() -> None:
    """The dispatcher emits exactly len(skynet_orders) Sends to the worker."""
    dispatcher = _get_dispatcher(_build(FakePlanner(_orders(1)), FakeEvaluator()))
    sends = dispatcher({"skynet_orders": _order_dicts(3)})
    assert isinstance(sends, list)
    assert len(sends) == 3
    assert all(s.node == "skynet_worker" for s in sends)
    assert [s.arg["_order"]["order_id"] for s in sends] == ["ord-0", "ord-1", "ord-2"]


def test_dispatcher_empty_orders_route_to_output() -> None:
    """No staged orders → the on_empty fallback (skynet_output)."""
    dispatcher = _get_dispatcher(_build(FakePlanner(_orders(1)), FakeEvaluator()))
    assert dispatcher({"skynet_orders": []}) == "skynet_output"
    assert dispatcher({}) == "skynet_output"


def test_dispatcher_disabled_routes_to_output(monkeypatch: Any) -> None:
    """settings.parallel_enabled=False short-circuits to on_empty."""
    from prismal.agents.patterns import parallel as parallel_module

    class _Disabled:
        parallel_enabled = False
        parallel_max_workers = 10

    monkeypatch.setattr(parallel_module, "get_settings", lambda: _Disabled())
    dispatcher = _get_dispatcher(_build(FakePlanner(_orders(1)), FakeEvaluator()))
    assert dispatcher({"skynet_orders": _order_dicts(3)}) == "skynet_output"


def test_dispatcher_caps_as_second_line_of_defense() -> None:
    """Even if over-staged, the dispatcher truncates at skynet_max_swarm."""
    dispatcher = _get_dispatcher(
        _build(FakePlanner(_orders(1)), FakeEvaluator(), skynet_max_swarm=2)
    )
    sends = dispatcher({"skynet_orders": _order_dicts(5)})
    assert len(sends) == 2


# ── registration (S4-05) ─────────────────────────────────────────────────────


async def test_register_skynet_is_idempotent() -> None:
    """register_skynet installs once and is a no-op afterwards."""
    registry = SubgraphRegistry()
    await register_skynet(registry, settings=Settings())
    first = registry.get("skynet")
    assert first is not None
    await register_skynet(registry, settings=Settings())
    assert registry.get("skynet") is first
