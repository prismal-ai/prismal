"""Tests for the Skynet value objects (Fase S — SPEC-SKY-TYP-001).

Phase S1 "done when": value objects round-trip.
"""

from __future__ import annotations

import dataclasses

import pytest

from prismal.agents.skynet.types import SwarmOrder, SwarmPlan, SwarmResult, WorkerResult

# ── SwarmOrder ───────────────────────────────────────────────────────────────


def test_swarm_order_round_trip() -> None:
    """All explicit fields round-trip unchanged."""
    order = SwarmOrder(
        order_id="ord-1",
        instruction="research competitor A",
        role="researcher",
        context={"region": "EU"},
        attempt=2,
    )
    assert order.order_id == "ord-1"
    assert order.instruction == "research competitor A"
    assert order.role == "researcher"
    assert order.context == {"region": "EU"}
    assert order.attempt == 2


def test_swarm_order_defaults() -> None:
    """role defaults to 'worker', context to {}, attempt to 1."""
    order = SwarmOrder(order_id="ord-1", instruction="do x")
    assert order.role == "worker"
    assert order.context == {}
    assert order.attempt == 1


def test_swarm_order_context_default_is_not_shared() -> None:
    """Each instance gets its own context dict (default_factory)."""
    a = SwarmOrder(order_id="a", instruction="x")
    b = SwarmOrder(order_id="b", instruction="y")
    assert a.context is not b.context


def test_swarm_order_is_frozen() -> None:
    """Value objects are immutable."""
    order = SwarmOrder(order_id="ord-1", instruction="do x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        order.attempt = 3  # type: ignore[misc]


# ── SwarmPlan ────────────────────────────────────────────────────────────────


def _orders(n: int) -> list[SwarmOrder]:
    return [SwarmOrder(order_id=f"ord-{i}", instruction=f"task {i}") for i in range(n)]


def test_swarm_plan_round_trip() -> None:
    """goal/orders/round/rationale round-trip unchanged."""
    plan = SwarmPlan(
        goal="research 3 things", orders=_orders(3), round=2, rationale="split by topic"
    )
    assert plan.goal == "research 3 things"
    assert len(plan.orders) == 3
    assert plan.round == 2
    assert plan.rationale == "split by topic"


def test_swarm_plan_defaults() -> None:
    """round defaults to 1, rationale to '', deferred to []."""
    plan = SwarmPlan(goal="g", orders=_orders(1))
    assert plan.round == 1
    assert plan.rationale == ""
    assert plan.deferred == []


def test_swarm_plan_carries_deferred_overflow() -> None:
    """Capped overflow orders ride on the plan's deferred set (RF-SKY-03)."""
    plan = SwarmPlan(goal="g", orders=_orders(2), deferred=_orders(3))
    assert plan.size == 2
    assert len(plan.deferred) == 3


def test_swarm_plan_size_is_order_count() -> None:
    """plan.size is the swarm size the supervisor chose (RF-SKY-02)."""
    assert SwarmPlan(goal="g", orders=_orders(5)).size == 5
    assert SwarmPlan(goal="g", orders=[]).size == 0


def test_swarm_plan_is_frozen() -> None:
    """Value objects are immutable."""
    plan = SwarmPlan(goal="g", orders=_orders(1))
    with pytest.raises(dataclasses.FrozenInstanceError):
        plan.goal = "other"  # type: ignore[misc]


# ── WorkerResult ─────────────────────────────────────────────────────────────


def test_worker_result_round_trip() -> None:
    """All explicit fields round-trip unchanged."""
    result = WorkerResult(
        order_id="ord-1",
        output="done",
        success=True,
        error=None,
        tool_calls=3,
    )
    assert result.order_id == "ord-1"
    assert result.output == "done"
    assert result.success is True
    assert result.error is None
    assert result.tool_calls == 3


def test_worker_result_defaults() -> None:
    """error defaults to None and tool_calls to 0."""
    result = WorkerResult(order_id="ord-1", output="", success=False)
    assert result.error is None
    assert result.tool_calls == 0


def test_worker_result_failure_carries_error() -> None:
    """A failed worker result captures the error message (RF-SKY-05)."""
    result = WorkerResult(order_id="ord-1", output="", success=False, error="boom")
    assert result.success is False
    assert result.error == "boom"


def test_worker_result_is_frozen() -> None:
    """Value objects are immutable."""
    result = WorkerResult(order_id="ord-1", output="x", success=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.success = False  # type: ignore[misc]


# ── SwarmResult ──────────────────────────────────────────────────────────────


def test_swarm_result_round_trip() -> None:
    """All fields round-trip unchanged."""
    worker_results = [WorkerResult(order_id="ord-1", output="a", success=True)]
    deferred = _orders(2)
    result = SwarmResult(
        goal="g",
        answer="synthesized",
        worker_results=worker_results,
        rounds_completed=2,
        deferred_orders=deferred,
        complete=True,
    )
    assert result.goal == "g"
    assert result.answer == "synthesized"
    assert result.worker_results == worker_results
    assert result.rounds_completed == 2
    assert result.deferred_orders == deferred
    assert result.complete is True


def test_swarm_result_is_frozen() -> None:
    """Value objects are immutable."""
    result = SwarmResult(
        goal="g",
        answer="a",
        worker_results=[],
        rounds_completed=1,
        deferred_orders=[],
        complete=False,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.complete = True  # type: ignore[misc]
