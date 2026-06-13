"""Tests for the budget config fields (Phase C — SPEC-CST-CFG-001)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from prismal.core.config import Settings

# ── Defaults (SPEC-CST-CFG-001) ──────────────────────────────────────────────


def test_budget_defaults_match_spec() -> None:
    s = Settings()
    assert s.budget_enabled is False
    assert s.budget_max_tokens == 0
    assert s.budget_max_cost_usd == 0.0
    assert s.budget_max_calls == 0
    assert s.budget_max_wall_clock_s == 0.0
    assert s.budget_scope == "turn"
    assert s.budget_soft_ratio == 0.8
    assert s.budget_hard_cap is True
    assert s.budget_pricing == {}


def test_budget_disabled_by_default_is_opt_in() -> None:
    assert Settings().budget_enabled is False


# ── Env parsing (PRISMAL_* prefix) ───────────────────────────────────────────


def test_budget_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRISMAL_BUDGET_ENABLED", "true")
    monkeypatch.setenv("PRISMAL_BUDGET_MAX_TOKENS", "50000")
    monkeypatch.setenv("PRISMAL_BUDGET_MAX_COST_USD", "2.5")
    monkeypatch.setenv("PRISMAL_BUDGET_MAX_CALLS", "20")
    monkeypatch.setenv("PRISMAL_BUDGET_MAX_WALL_CLOCK_S", "60")
    monkeypatch.setenv("PRISMAL_BUDGET_SCOPE", "session")
    monkeypatch.setenv("PRISMAL_BUDGET_SOFT_RATIO", "0.5")
    monkeypatch.setenv("PRISMAL_BUDGET_HARD_CAP", "false")

    s = Settings()
    assert s.budget_enabled is True
    assert s.budget_max_tokens == 50_000
    assert s.budget_max_cost_usd == 2.5
    assert s.budget_max_calls == 20
    assert s.budget_max_wall_clock_s == 60.0
    assert s.budget_scope == "session"
    assert s.budget_soft_ratio == 0.5
    assert s.budget_hard_cap is False


def test_budget_pricing_table_from_kwarg() -> None:
    s = Settings(budget_pricing={"gpt-4o": {"input": 2.5, "output": 10.0}})
    assert s.budget_pricing["gpt-4o"]["input"] == 2.5
    assert s.budget_pricing["gpt-4o"]["output"] == 10.0


# ── Validation (_validate_budget) ────────────────────────────────────────────


def test_negative_dimensions_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(budget_max_tokens=-1)
    with pytest.raises(ValidationError):
        Settings(budget_max_cost_usd=-0.5)


def test_soft_ratio_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(budget_soft_ratio=1.5)
    with pytest.raises(ValidationError):
        Settings(budget_soft_ratio=-0.1)


def test_invalid_scope_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(budget_scope="galaxy")


def test_valid_scopes_accepted() -> None:
    for scope in ("turn", "session", "tenant"):
        assert Settings(budget_scope=scope).budget_scope == scope
