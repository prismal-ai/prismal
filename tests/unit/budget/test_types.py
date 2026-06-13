"""Tests for the budget value objects (Phase C — SPEC-CST-TYP-001).

C1 "done when": value objects round-trip; Budget(0,..).is_unlimited;
Usage.__add__ correct.
"""

from __future__ import annotations

import dataclasses

import pytest

from prismal.budget.types import (
    Budget,
    BudgetScope,
    BudgetStatus,
    Degradation,
    TokenCounts,
    Usage,
)

# ── Budget ────────────────────────────────────────────────────────────────────


def test_budget_defaults_are_unlimited() -> None:
    """An all-zero budget is unlimited on every dimension."""
    b = Budget()
    assert b.max_tokens == 0
    assert b.max_cost_usd == 0.0
    assert b.max_calls == 0
    assert b.max_wall_clock_s == 0.0
    assert b.scope is BudgetScope.TURN
    assert b.is_unlimited is True


def test_budget_with_any_limit_is_not_unlimited() -> None:
    """A non-zero on any single dimension flips is_unlimited off."""
    assert Budget(max_tokens=100).is_unlimited is False
    assert Budget(max_cost_usd=1.0).is_unlimited is False
    assert Budget(max_calls=5).is_unlimited is False
    assert Budget(max_wall_clock_s=30.0).is_unlimited is False


def test_budget_is_frozen() -> None:
    """Budget is an immutable value object."""
    b = Budget(max_tokens=10)
    with pytest.raises(dataclasses.FrozenInstanceError):
        b.max_tokens = 20  # type: ignore[misc]


def test_budget_scope_round_trips() -> None:
    b = Budget(scope=BudgetScope.TENANT)
    assert b.scope is BudgetScope.TENANT
    assert BudgetScope("session") is BudgetScope.SESSION


# ── TokenCounts ───────────────────────────────────────────────────────────────


def test_token_counts_total() -> None:
    tc = TokenCounts(prompt_tokens=30, completion_tokens=12)
    assert tc.total == 42


def test_token_counts_defaults_zero() -> None:
    tc = TokenCounts()
    assert tc.prompt_tokens == 0
    assert tc.completion_tokens == 0
    assert tc.total == 0


# ── Usage ─────────────────────────────────────────────────────────────────────


def test_usage_total_tokens() -> None:
    u = Usage(prompt_tokens=100, completion_tokens=40)
    assert u.total_tokens == 140


def test_usage_add_is_dimension_wise() -> None:
    """__add__ sums each numeric dimension independently."""
    a = Usage(prompt_tokens=10, completion_tokens=5, cost_usd=0.01, calls=1, wall_clock_s=2.0)
    b = Usage(prompt_tokens=3, completion_tokens=2, cost_usd=0.02, calls=1, wall_clock_s=1.5)
    s = a + b
    assert s.prompt_tokens == 13
    assert s.completion_tokens == 7
    assert s.cost_usd == pytest.approx(0.03)
    assert s.calls == 2
    assert s.wall_clock_s == pytest.approx(3.5)
    assert s.total_tokens == 20


def test_usage_add_ors_estimated_flag() -> None:
    """Any estimated operand makes the sum estimated."""
    measured = Usage(cost_usd=0.01, estimated=False)
    estimated = Usage(cost_usd=0.02, estimated=True)
    assert (measured + measured).estimated is False
    assert (measured + estimated).estimated is True
    assert (estimated + measured).estimated is True


def test_usage_is_frozen() -> None:
    u = Usage(calls=1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        u.calls = 9  # type: ignore[misc]


# ── BudgetStatus / Degradation ────────────────────────────────────────────────


def test_budget_status_fields() -> None:
    st = BudgetStatus(
        within=False,
        soft_exceeded=True,
        hard_exceeded=False,
        breached_dimension="tokens",
        usage=Usage(prompt_tokens=90),
        budget=Budget(max_tokens=100),
    )
    assert st.within is False
    assert st.soft_exceeded is True
    assert st.hard_exceeded is False
    assert st.breached_dimension == "tokens"


def test_degradation_defaults() -> None:
    d = Degradation()
    assert d.terminate is False
    assert d.reduce is False
    assert d.reason == ""
