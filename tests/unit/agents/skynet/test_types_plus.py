"""Unit tests for the Skynet S+ value-object extensions (SPEC-SP-TYP-001).

Additive fields only: ``WorkerResult.usage/role/remote`` and ``SwarmResult.usage``
default to an empty :class:`Usage` / ``"worker"`` / ``False`` so every Phase-S
round-trip is preserved.
"""

from __future__ import annotations

from prismal.agents.skynet.types import SwarmOrder, WorkerResult
from prismal.budget.types import Usage


def test_worker_result_usage_defaults_and_roundtrip() -> None:
    # Phase-S positional construction still works — new fields default.
    result = WorkerResult(order_id="ord-1", output="done", success=True)
    assert result.usage == Usage()
    assert result.role == "worker"
    assert result.remote is False

    # S+ fields round-trip when supplied.
    usage = Usage(prompt_tokens=10, completion_tokens=5, calls=1)
    enriched = WorkerResult(
        order_id="ord-2",
        output="ok",
        success=True,
        usage=usage,
        role="researcher",
        remote=True,
    )
    assert enriched.usage.total_tokens == 15
    assert enriched.role == "researcher"
    assert enriched.remote is True


def test_swarm_result_usage_default_and_roundtrip() -> None:
    from prismal.agents.skynet.types import SwarmResult

    order = SwarmOrder(order_id="ord-1", instruction="x")
    worker = WorkerResult(order_id="ord-1", output="y", success=True)

    # Default: empty swarm usage.
    swarm = SwarmResult(
        goal="g",
        answer="a",
        worker_results=[worker],
        rounds_completed=1,
        deferred_orders=[order],
        complete=True,
    )
    assert swarm.usage == Usage()

    # Supplied whole-swarm usage round-trips.
    total = Usage(prompt_tokens=100, completion_tokens=40, calls=4)
    metered = SwarmResult(
        goal="g",
        answer="a",
        worker_results=[worker],
        rounds_completed=1,
        deferred_orders=[],
        complete=True,
        usage=total,
    )
    assert metered.usage.total_tokens == 140
