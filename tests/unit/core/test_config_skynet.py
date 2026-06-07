"""Tests for the Skynet config fields (Fase S — SPEC-SKY-CFG-001).

Phase S1 "done when": settings parse from ``PRISMAL_*`` env vars, the defaults
match SPEC-SKY-CFG-001, ``skynet_max_swarm`` is clamped to
``parallel_max_workers``, and a fixed swarm size above the cap raises
``SkynetConfigError``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from prismal.core.config import Settings
from prismal.core.exceptions import SkynetConfigError

# ── Defaults (SPEC-SKY-CFG-001) ──────────────────────────────────────────────


def test_skynet_defaults_match_spec() -> None:
    """All Skynet fields default to the SPEC-SKY-CFG-001 values."""
    s = Settings()
    assert s.skynet_enabled is False
    assert s.skynet_swarm_size == 0
    assert s.skynet_max_swarm == 8
    assert s.skynet_max_rounds == 3
    assert s.skynet_reduce_strategy == "synthesis"
    assert s.skynet_worker_model == ""
    assert s.skynet_planner_model == ""
    assert s.skynet_token_budget == 0


def test_skynet_disabled_by_default_is_opt_in() -> None:
    """The master toggle is off by default (RF-SKY-10)."""
    assert Settings().skynet_enabled is False


# ── Env parsing (PRISMAL_* prefix) ───────────────────────────────────────────


def test_skynet_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every Skynet field parses from its PRISMAL_* env var."""
    monkeypatch.setenv("PRISMAL_SKYNET_ENABLED", "true")
    monkeypatch.setenv("PRISMAL_SKYNET_SWARM_SIZE", "4")
    monkeypatch.setenv("PRISMAL_SKYNET_MAX_SWARM", "6")
    monkeypatch.setenv("PRISMAL_SKYNET_MAX_ROUNDS", "5")
    monkeypatch.setenv("PRISMAL_SKYNET_REDUCE_STRATEGY", "concat")
    monkeypatch.setenv("PRISMAL_SKYNET_WORKER_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("PRISMAL_SKYNET_PLANNER_MODEL", "claude-sonnet-4-5")
    monkeypatch.setenv("PRISMAL_SKYNET_TOKEN_BUDGET", "100000")

    s = Settings()
    assert s.skynet_enabled is True
    assert s.skynet_swarm_size == 4
    assert s.skynet_max_swarm == 6
    assert s.skynet_max_rounds == 5
    assert s.skynet_reduce_strategy == "concat"
    assert s.skynet_worker_model == "gpt-4o-mini"
    assert s.skynet_planner_model == "claude-sonnet-4-5"
    assert s.skynet_token_budget == 100_000


# ── Field validation (S1-04) ─────────────────────────────────────────────────


def test_skynet_swarm_size_rejects_negative() -> None:
    """skynet_swarm_size must be ≥ 0 (0 = dynamic sizing)."""
    with pytest.raises(ValidationError):
        Settings(skynet_swarm_size=-1)


@pytest.mark.parametrize("value", [0, -3])
def test_skynet_max_swarm_must_be_positive(value: int) -> None:
    """skynet_max_swarm rejects zero and negatives."""
    with pytest.raises(ValidationError):
        Settings(skynet_max_swarm=value)


def test_skynet_max_rounds_must_be_positive() -> None:
    """skynet_max_rounds rejects zero and negatives."""
    with pytest.raises(ValidationError):
        Settings(skynet_max_rounds=0)


def test_skynet_token_budget_rejects_negative() -> None:
    """skynet_token_budget must be ≥ 0 (0 = unlimited)."""
    with pytest.raises(ValidationError):
        Settings(skynet_token_budget=-1)


def test_skynet_reduce_strategy_rejects_unknown() -> None:
    """skynet_reduce_strategy only accepts synthesis|concat|first_success."""
    with pytest.raises(ValidationError):
        Settings(skynet_reduce_strategy="majority")


@pytest.mark.parametrize("strategy", ["synthesis", "concat", "first_success"])
def test_skynet_reduce_strategy_accepts_spec_values(strategy: str) -> None:
    """All three SPEC reduce strategies are valid."""
    assert Settings(skynet_reduce_strategy=strategy).skynet_reduce_strategy == strategy


# ── Cross-field validation (S1-04 / RF-SKY-03) ──────────────────────────────


def test_skynet_max_swarm_clamped_to_parallel_max_workers() -> None:
    """The operator ceiling parallel_max_workers always wins (clamp, not error)."""
    s = Settings(skynet_max_swarm=50, parallel_max_workers=10)
    assert s.skynet_max_swarm == 10


def test_skynet_max_swarm_below_ceiling_is_untouched() -> None:
    """No clamping when skynet_max_swarm already fits the ceiling."""
    s = Settings(skynet_max_swarm=4, parallel_max_workers=10)
    assert s.skynet_max_swarm == 4


def test_skynet_fixed_size_above_cap_raises_config_error() -> None:
    """A fixed swarm size larger than the effective cap is a config error."""
    with pytest.raises(SkynetConfigError):
        Settings(skynet_swarm_size=20, skynet_max_swarm=8, parallel_max_workers=10)


def test_skynet_fixed_size_above_clamped_cap_raises_config_error() -> None:
    """The cap compared against is the *clamped* one (parallel_max_workers wins)."""
    with pytest.raises(SkynetConfigError):
        Settings(skynet_swarm_size=8, skynet_max_swarm=8, parallel_max_workers=4)


def test_skynet_fixed_size_at_cap_is_accepted() -> None:
    """A fixed size equal to the effective cap is valid."""
    s = Settings(skynet_swarm_size=8, skynet_max_swarm=8, parallel_max_workers=10)
    assert s.skynet_swarm_size == 8


def test_skynet_dynamic_size_never_raises() -> None:
    """skynet_swarm_size=0 (dynamic) is always valid regardless of caps."""
    s = Settings(skynet_swarm_size=0, skynet_max_swarm=8, parallel_max_workers=1)
    assert s.skynet_swarm_size == 0
    assert s.skynet_max_swarm == 1
