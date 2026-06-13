"""Cost & budget governance demo with injected fakes (Phase C).

Drives the budget engine without any LLM or network:

1. Meter fake LLM responses into a ``CostMeter`` and watch ``Usage`` accumulate.
2. Show a ``BudgetGuard`` move through within → soft → hard as spend grows.
3. Hand a pattern (``reflection_loop``) the ``budget_guard_fn`` adapter and watch
   a soft cap stop it early.

Run::

    python examples/budget_governance.py
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from prismal.agents.patterns.reflection import reflection_loop
from prismal.budget import (
    Budget,
    BudgetGuard,
    CostMeter,
    Usage,
    make_budget_guard_fn,
)
from prismal.core.config import Settings


def _fake_response(prompt_tokens: int, completion_tokens: int) -> SimpleNamespace:
    """A stand-in for a LangChain AIMessage carrying usage metadata."""
    return SimpleNamespace(
        usage_metadata={"input_tokens": prompt_tokens, "output_tokens": completion_tokens}
    )


async def main() -> None:
    # A pricing table so the offline demo produces a deterministic USD cost.
    settings = Settings(budget_pricing={"demo/model": {"input": 1.0, "output": 2.0}})

    # ── 1. Meter fake responses ───────────────────────────────────────────────
    meter = CostMeter(settings=settings)
    budget = Budget(max_tokens=4_000, max_cost_usd=10.0, max_calls=5)
    guard = BudgetGuard(budget, meter, soft_ratio=0.8)

    print("Budget:", budget, "\n")
    for i in range(1, 6):
        usage = meter.record_response(_fake_response(500, 200), "demo/model", agent="demo")
        status = guard.check()
        level = "HARD" if status.hard_exceeded else "soft" if status.soft_exceeded else "ok"
        print(
            f"call {i}: tokens={usage.total_tokens:>5} "
            f"cost=${usage.cost_usd:5.2f} calls={usage.calls} → {level}"
            + (f" (breach: {status.breached_dimension})" if level != "ok" else "")
        )
        if status.hard_exceeded:
            print("  → hard cap reached: stop and return best effort\n")
            break

    # ── 2. A pattern degrades on a soft cap via the guard adapter ─────────────
    # Pre-load a meter to 85% of a 100-token budget so the guard is already soft.
    tight_meter = CostMeter()
    tight_meter.record(Usage(prompt_tokens=85))
    tight = BudgetGuard(Budget(max_tokens=100), tight_meter, soft_ratio=0.8, hard_cap=False)
    guard_fn = make_budget_guard_fn(tight)

    gen_calls = {"n": 0}

    async def generate(state: object, **kw: object) -> str:
        gen_calls["n"] += 1
        return f"draft v{gen_calls['n']}"

    async def critique(draft: str, state: object) -> tuple[str, float]:
        return ("keep refining", 0.1)  # never meets threshold → would loop

    best, score = await reflection_loop(
        generate, critique, {}, threshold=0.9, max_iterations=5, budget_guard_fn=guard_fn
    )
    print(f"reflection: generated {gen_calls['n']} draft(s) before the budget stopped it")
    print(f"           best={best!r} score={score}")


if __name__ == "__main__":
    asyncio.run(main())
