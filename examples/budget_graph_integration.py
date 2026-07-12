"""Budget seeding at the graph seam — seed, guard, degrade, abort, clear (Phase C).

Shows how a host wires cost & budget governance around one graph run, fully
offline: build ``Settings`` with ``budget_enabled=True`` and small caps, create
an ``AgentState``, seed the per-run ``{meter, guard}`` engine (kept in an
in-process registry — never in checkpointed state), retrieve the ``BudgetGuard``
at the node seam, meter fake LLM responses until the soft and then the hard
threshold fire, and release the engine afterwards.

Run::

    python examples/budget_graph_integration.py
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from prismal.agents.state import create_initial_state
from prismal.budget.guard import make_budget_guard_fn
from prismal.budget.resolve import (
    clear_budget_run,
    get_budget_guard,
    resolve_budget,
    seed_budget_run,
)
from prismal.core.config import Settings
from prismal.core.exceptions import BudgetExceeded


def _fake_response(prompt_tokens: int, completion_tokens: int) -> SimpleNamespace:
    """A stand-in for a LangChain AIMessage carrying ``usage_metadata``."""
    return SimpleNamespace(
        usage_metadata={"input_tokens": prompt_tokens, "output_tokens": completion_tokens}
    )


async def main() -> None:
    """Seed a budget run for one state, drive it soft → hard, then clear it."""
    # Small caps so the demo trips them quickly; the pricing table makes the
    # offline cost deterministic (no provider-authoritative figure available).
    settings = Settings(
        budget_enabled=True,
        budget_max_tokens=1_000,
        budget_max_calls=10,
        budget_soft_ratio=0.8,
        budget_pricing={"demo/model": {"input": 1.0, "output": 2.0}},
    )
    print("Resolved budget:", resolve_budget(settings), "\n")

    # ── Seed the per-run engine at the graph entry seam ───────────────────────
    state = create_initial_state(session_id="sess-budget-demo")
    seed_budget_run(state, settings)
    # Only a serializable marker reaches state; the live engine stays in-process.
    print("Checkpoint-safe marker:", state["metadata"]["budget"])

    guard = get_budget_guard(state)
    if guard is None:  # only possible when budget_enabled is False / unseeded
        raise RuntimeError("budget engine was not seeded")
    guard_fn = make_budget_guard_fn(guard)  # what the expensive patterns consume
    meter = guard.meter

    # ── Meter fake responses: 400 tokens/call → ok, soft (80%), hard ──────────
    print("\nMetering 400-token calls against the 1000-token ceiling:")
    for call in range(1, 4):
        usage = meter.record_response(_fake_response(300, 100), "demo/model", agent="coder")
        status = guard.check()
        level = "HARD" if status.hard_exceeded else "soft" if status.soft_exceeded else "ok"
        print(
            f"  call {call}: tokens={usage.total_tokens:>5} cost=${usage.cost_usd:.2f} "
            f"calls={usage.calls} → {level}"
            + (f" (breach: {status.breached_dimension})" if level != "ok" else "")
        )
        if status.soft_exceeded:
            print(f"    degradation advice: {guard.degradation()}")
        if status.hard_exceeded:
            print(f"    degradation advice: {guard.degradation()}")
            break

    # ── The node seam consults guard_fn before the next LLM call ──────────────
    # hard cap + hard_cap=True (default) → enforce() audits and raises.
    try:
        await guard_fn({"agent": "coder"})
    except BudgetExceeded as exc:
        print(
            f"\nBudgetExceeded raised at the seam: dimension={exc.dimension} "
            f"used={exc.used:.0f} limit={exc.limit:.0f} scope={exc.scope}"
        )

    # ── Release the per-run engine when the run finishes ──────────────────────
    clear_budget_run(state)
    print("After clear_budget_run:", get_budget_guard(state))


if __name__ == "__main__":
    asyncio.run(main())
