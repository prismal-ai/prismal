"""Tests for budget exceptions (Phase C — SPEC-CST-ERR-001)."""

from __future__ import annotations

import pytest

from prismal.core.exceptions import (
    BudgetExceeded,
    PrismalError,
    SkynetBudgetExceeded,
    SkynetError,
)


def test_budget_exceeded_carries_context() -> None:
    err = BudgetExceeded("tokens", used=1200, limit=1000, scope="turn")
    assert err.dimension == "tokens"
    assert err.used == 1200
    assert err.limit == 1000
    assert err.scope == "turn"
    assert "tokens" in str(err)
    assert "turn" in str(err)


def test_budget_exceeded_is_prismal_error() -> None:
    assert issubclass(BudgetExceeded, PrismalError)


def test_budget_exceeded_default_scope() -> None:
    err = BudgetExceeded("cost", used=2.0, limit=1.0)
    assert err.scope == "turn"


def test_skynet_budget_exceeded_is_both_budget_and_skynet() -> None:
    """Unification: catchable as a BudgetExceeded AND a SkynetError."""
    assert issubclass(SkynetBudgetExceeded, BudgetExceeded)
    assert issubclass(SkynetBudgetExceeded, SkynetError)


def test_skynet_budget_exceeded_sets_token_dimension() -> None:
    err = SkynetBudgetExceeded(used=50_000, limit=40_000)
    assert err.dimension == "tokens"
    assert err.used == 50_000
    assert err.limit == 40_000
    assert err.scope == "skynet"


def test_skynet_budget_exceeded_caught_as_budget_exceeded() -> None:
    with pytest.raises(BudgetExceeded):
        raise SkynetBudgetExceeded(used=10, limit=5)
