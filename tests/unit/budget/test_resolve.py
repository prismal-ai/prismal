"""Tests for budget resolution & per-run seeding (Phase C — SPEC-CST-RES-001)."""

from __future__ import annotations

from prismal.budget.guard import BudgetGuard
from prismal.budget.resolve import get_budget_guard, resolve_budget, seed_budget_run
from prismal.budget.types import BudgetScope
from prismal.core.config import Settings

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
    state: dict = {"metadata": {}}
    seed_budget_run(state, Settings(budget_enabled=False))
    assert "budget" not in state["metadata"]
    assert get_budget_guard(state) is None


def test_seed_installs_meter_and_guard_when_enabled() -> None:
    state: dict = {"metadata": {}}
    seed_budget_run(state, Settings(budget_enabled=True, budget_max_tokens=500))
    guard = get_budget_guard(state)
    assert isinstance(guard, BudgetGuard)
    assert guard.budget.max_tokens == 500
    assert guard.meter is not None


def test_seed_creates_metadata_if_absent() -> None:
    state: dict = {}
    seed_budget_run(state, Settings(budget_enabled=True))
    assert "budget" in state["metadata"]


def test_seed_respects_hard_cap_setting() -> None:
    state: dict = {"metadata": {}}
    seed_budget_run(state, Settings(budget_enabled=True, budget_hard_cap=False))
    guard = get_budget_guard(state)
    assert guard is not None
    assert guard.hard_cap is False


def test_get_guard_none_on_unseeded_state() -> None:
    assert get_budget_guard({}) is None
    assert get_budget_guard({"metadata": {}}) is None
