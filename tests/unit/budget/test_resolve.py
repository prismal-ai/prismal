"""Tests for budget resolution & per-run seeding (Phase C — SPEC-CST-RES-001).

The per-run engine lives in an in-process registry keyed by ``session_id`` — never
in checkpointed state — so a live ``CostMeter`` (which may hold a SQLite-backed
``CostTracker``) never reaches the checkpoint serializer.
"""

from __future__ import annotations

import pytest

from prismal.budget.guard import BudgetGuard
from prismal.budget.resolve import (
    clear_budget_run,
    get_budget_guard,
    maybe_seed_budget_run,
    resolve_budget,
    seed_budget_run,
)
from prismal.budget.types import BudgetScope, Usage
from prismal.core.config import Settings


class _Human:
    """Minimal stand-in for a LangChain HumanMessage (``.type == 'human'``)."""

    type = "human"


class _AI:
    type = "ai"


def _state_msgs(session_id: str, n_human: int) -> dict:
    msgs: list[object] = [_Human() for _ in range(n_human)]
    msgs.append(_AI())
    return {"session_id": session_id, "messages": msgs, "metadata": {}}


@pytest.fixture(autouse=True)
def _isolate_registry() -> None:
    """Each test starts and ends with an empty run registry."""
    from prismal.budget import resolve as resolve_mod

    resolve_mod._RUN_ENGINES.clear()
    yield
    resolve_mod._RUN_ENGINES.clear()


def _state(session_id: str | None = "sess-1") -> dict:
    return {"session_id": session_id, "metadata": {}}


# ── resolve_budget ────────────────────────────────────────────────────────────


def test_resolve_budget_maps_settings() -> None:
    s = Settings(
        budget_max_tokens=1000,
        budget_max_cost_usd=2.0,
        budget_max_calls=10,
        budget_max_wall_clock_s=30.0,
        budget_scope="session",
    )
    b = resolve_budget(s)
    assert b.max_tokens == 1000
    assert b.max_cost_usd == 2.0
    assert b.max_calls == 10
    assert b.max_wall_clock_s == 30.0
    assert b.scope is BudgetScope.SESSION


def test_resolve_budget_defaults_unlimited() -> None:
    assert resolve_budget(Settings()).is_unlimited is True


# ── seed_budget_run / get_budget_guard ────────────────────────────────────────


def test_seed_is_noop_when_disabled() -> None:
    state = _state()
    seed_budget_run(state, Settings(budget_enabled=False))
    assert get_budget_guard(state) is None
    # Nothing heavy was written into the checkpointed state.
    assert "budget" not in state["metadata"]


def test_seed_installs_retrievable_guard_when_enabled() -> None:
    state = _state()
    seed_budget_run(state, Settings(budget_enabled=True, budget_max_tokens=500))
    guard = get_budget_guard(state)
    assert isinstance(guard, BudgetGuard)
    assert guard.budget.max_tokens == 500
    assert guard.meter is not None


def test_no_live_objects_in_checkpointed_state() -> None:
    """The state must stay serializable — only primitives under metadata."""
    state = _state()
    seed_budget_run(state, Settings(budget_enabled=True))
    bucket = state["metadata"].get("budget", {})
    for value in bucket.values():
        assert isinstance(value, (str, int, float, bool))


def test_guard_shared_across_nodes_same_session() -> None:
    """A second state with the same session_id sees the same live guard."""
    seed_budget_run(_state("sess-X"), Settings(budget_enabled=True, budget_max_tokens=9))
    again = get_budget_guard(_state("sess-X"))
    assert again is not None
    assert again.budget.max_tokens == 9


def test_distinct_sessions_isolated() -> None:
    seed_budget_run(_state("a"), Settings(budget_enabled=True, budget_max_tokens=1))
    seed_budget_run(_state("b"), Settings(budget_enabled=True, budget_max_tokens=2))
    assert get_budget_guard(_state("a")).budget.max_tokens == 1  # type: ignore[union-attr]
    assert get_budget_guard(_state("b")).budget.max_tokens == 2  # type: ignore[union-attr]


def test_seed_respects_hard_cap_setting() -> None:
    seed_budget_run(_state(), Settings(budget_enabled=True, budget_hard_cap=False))
    guard = get_budget_guard(_state())
    assert guard is not None
    assert guard.hard_cap is False


def test_get_guard_none_on_unseeded_state() -> None:
    assert get_budget_guard({}) is None
    assert get_budget_guard(_state("never-seeded")) is None


def test_clear_budget_run_removes_engine() -> None:
    state = _state("sess-clear")
    seed_budget_run(state, Settings(budget_enabled=True))
    assert get_budget_guard(state) is not None
    clear_budget_run(state)
    assert get_budget_guard(state) is None


# ── maybe_seed_budget_run (once-per-turn) ─────────────────────────────────────


def test_maybe_seed_noop_when_disabled() -> None:
    state = _state_msgs("s", 1)
    maybe_seed_budget_run(state, Settings(budget_enabled=False))
    assert get_budget_guard(state) is None


def test_maybe_seed_preserves_meter_within_a_turn() -> None:
    """Repeated supervisor hops in one turn keep accumulating (no reset)."""
    settings = Settings(budget_enabled=True, budget_max_tokens=10_000)
    state = _state_msgs("s", 1)
    maybe_seed_budget_run(state, settings)
    guard1 = get_budget_guard(state)
    assert guard1 is not None
    guard1.meter.record(Usage(prompt_tokens=100, calls=1))

    # Same turn (same human-message count): the engine must be preserved.
    maybe_seed_budget_run(state, settings)
    guard2 = get_budget_guard(state)
    assert guard2 is guard1
    assert guard2.meter.usage.total_tokens == 100


def test_maybe_seed_resets_on_new_turn() -> None:
    """A new user turn (more HumanMessages) gets a fresh meter."""
    settings = Settings(budget_enabled=True, budget_max_tokens=10_000)
    state1 = _state_msgs("s", 1)
    maybe_seed_budget_run(state1, settings)
    get_budget_guard(state1).meter.record(Usage(prompt_tokens=500, calls=1))  # type: ignore[union-attr]

    state2 = _state_msgs("s", 2)  # next turn
    maybe_seed_budget_run(state2, settings)
    guard = get_budget_guard(state2)
    assert guard is not None
    assert guard.meter.usage.total_tokens == 0  # fresh meter
