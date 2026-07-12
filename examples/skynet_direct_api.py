"""Direct Skynet layer demo — plan → workers → reduce → evaluate, no subgraph (Fase S).

Drives ``SkynetSupervisor``, ``SwarmWorker`` and ``reduce_results`` directly with
deterministic injected backends — no LLM, no network, no LangGraph. One order
fails on its first attempt, so the demo also shows the supervisor's deterministic
re-plan of unmet orders (pass-through with ``attempt + 1``; the planner backend
is not consulted on a re-plan round).

Run::

    python examples/skynet_direct_api.py
"""

from __future__ import annotations

import asyncio
from typing import Any

from prismal.agents.skynet.reduce import reduce_results
from prismal.agents.skynet.supervisor import SkynetSupervisor
from prismal.agents.skynet.types import SwarmOrder, SwarmPlan, WorkerResult
from prismal.agents.skynet.worker import SwarmWorker
from prismal.core.config import Settings

GOAL = "Research competitors Acme, Globex and Initech in parallel"

_COMPETITORS = ["Acme", "Globex", "Initech"]


class _ConsoleAudit:
    """In-memory stand-in for AuditLogger — prints hash-first records to stdout."""

    def log_event(self, event: str, payload: dict[str, Any]) -> None:
        compact = {k: v for k, v in payload.items() if not k.endswith("_hash")}
        print(f"  [audit] {event}: {compact}")


async def fake_plan(messages: list[dict[str, str]]) -> SwarmPlan:
    """Deterministic stand-in for the planner LLM call (one order per competitor).

    A real deployment omits ``plan_fn`` so the supervisor lazily wires
    ``ProviderRegistry().get_llm()`` honouring ``settings.skynet_planner_model``.
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
    """Deterministic stand-in for the per-order worker LLM call.

    Fails ord-2 on its first attempt only — the exception is captured by
    ``SwarmWorker.execute`` as ``WorkerResult(success=False)`` (never raised out
    of the node), which drives the re-plan round below.
    """
    user = messages[1]["content"]  # SecurePromptBuilder output: [system, user]
    if "ord-2" in user and "attempt 1" in user:
        raise RuntimeError("upstream rate limited")
    name = next((n for n in _COMPETITORS if n in user), "unknown")
    return f"{name}: strong in EU, weak mobile offering, pricing mid-market."


async def fake_evaluate(messages: list[dict[str, str]]) -> tuple[bool, str]:
    """Deterministic stand-in for the evaluator LLM call.

    Declares the goal unmet while any worker result is marked FAILED; otherwise
    it synthesizes the final answer.
    """
    if "FAILED" in messages[1]["content"]:
        return False, "Partial: one competitor is still missing — re-plan needed."
    return (
        True,
        "All three competitors researched: each is strong regionally but "
        "under-invested in mobile — a clear differentiation opportunity.",
    )


def _print_results(results: list[WorkerResult]) -> None:
    for result in results:
        status = "ok" if result.success else f"FAILED ({result.error})"
        print(f"  [{result.order_id} / {status}] {result.output}")


async def main() -> None:
    """Plan, fan out fake workers, reduce, evaluate, and re-plan one unmet order."""
    settings = Settings()
    audit = _ConsoleAudit()
    supervisor = SkynetSupervisor(
        plan_fn=fake_plan,
        evaluate_fn=fake_evaluate,
        audit=audit,  # type: ignore[arg-type] — duck-typed audit stand-in
        settings=settings,
    )
    worker = SwarmWorker(worker_fn=fake_worker, settings=settings)

    # ── Round 1: plan → workers → reduce → evaluate ───────────────────────────
    print(f"Goal: {GOAL}\n")
    plan = await supervisor.plan(GOAL)
    print(f"Round {plan.round}: {plan.size} order(s) — {plan.rationale}")
    print(f"  deferred beyond the cap: {len(plan.deferred)}")

    results = list(await asyncio.gather(*(worker.execute(order) for order in plan.orders)))
    _print_results(results)

    # LLM-free reduction strategies: "concat" joins successes keyed by order id;
    # "first_success" returns the earliest successful output.
    concat = await reduce_results(GOAL, results, strategy="concat", settings=settings)
    first = await reduce_results(GOAL, results, strategy="first_success", settings=settings)
    print(f"\nreduce(concat):\n{concat}")
    print(f"\nreduce(first_success): {first}")

    complete, answer = await supervisor.evaluate(GOAL, results)
    print(f"\nevaluate → complete={complete}: {answer}\n")

    # ── Round 2: deterministic re-plan of the unmet order ─────────────────────
    failed_ids = {r.order_id for r in results if not r.success}
    unmet = [o for o in plan.orders if o.order_id in failed_ids]
    replan = await supervisor.plan(GOAL, round=2, unmet=unmet)
    print(f"Round {replan.round}: {replan.size} order(s) — {replan.rationale}")
    for order in replan.orders:
        print(f"  [{order.order_id}] attempt={order.attempt}: {order.instruction}")

    retries = list(await asyncio.gather(*(worker.execute(order) for order in replan.orders)))
    _print_results(retries)

    all_results = [r for r in results if r.success] + retries
    final = await reduce_results(GOAL, all_results, strategy="concat", settings=settings)
    complete, answer = await supervisor.evaluate(GOAL, all_results)

    print(f"\nreduce(concat) after re-plan:\n{final}")
    print(f"\nevaluate → complete={complete}\nFinal answer: {answer}")


if __name__ == "__main__":
    asyncio.run(main())
