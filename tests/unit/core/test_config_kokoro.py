"""Tests for the Kokoro / souls config fields (Fase K — SPEC-KOK-CFG-001).

Phase K2 "done when": settings parse from ``PRISMAL_*`` env vars and the
defaults match SPEC-KOK-CFG-001.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from prismal.core.config import Settings

# ── Defaults (SPEC-KOK-CFG-001) ──────────────────────────────────────────────


def test_kokoro_defaults_match_spec() -> None:
    """All Kokoro fields default to the SPEC-KOK-CFG-001 values."""
    s = Settings()
    assert s.kokoro_enabled is False
    assert s.souls_dir == ""
    assert s.kokoro_souls == ["spirit", "mind", "heart"]
    assert s.kokoro_max_rounds == 2
    assert s.kokoro_agreement_threshold == 0.6
    assert s.kokoro_execute_actions is False
    assert s.kokoro_judge_model == ""
    assert s.soul_max_body_chars == 20_000


def test_kokoro_disabled_by_default_is_opt_in() -> None:
    """The master toggle and action execution are both off by default."""
    s = Settings()
    assert s.kokoro_enabled is False
    assert s.kokoro_execute_actions is False


# ── Env parsing (PRISMAL_* prefix) ───────────────────────────────────────────


def test_kokoro_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every Kokoro field parses from its PRISMAL_* env var."""
    monkeypatch.setenv("PRISMAL_KOKORO_ENABLED", "true")
    monkeypatch.setenv("PRISMAL_SOULS_DIR", "/tmp/souls")
    monkeypatch.setenv("PRISMAL_KOKORO_SOULS", '["risk", "growth", "customer"]')
    monkeypatch.setenv("PRISMAL_KOKORO_MAX_ROUNDS", "5")
    monkeypatch.setenv("PRISMAL_KOKORO_AGREEMENT_THRESHOLD", "0.85")
    monkeypatch.setenv("PRISMAL_KOKORO_EXECUTE_ACTIONS", "true")
    monkeypatch.setenv("PRISMAL_KOKORO_JUDGE_MODEL", "claude-sonnet-4-5")
    monkeypatch.setenv("PRISMAL_SOUL_MAX_BODY_CHARS", "5000")

    s = Settings()
    assert s.kokoro_enabled is True
    assert s.souls_dir == "/tmp/souls"
    assert s.kokoro_souls == ["risk", "growth", "customer"]
    assert s.kokoro_max_rounds == 5
    assert s.kokoro_agreement_threshold == 0.85
    assert s.kokoro_execute_actions is True
    assert s.kokoro_judge_model == "claude-sonnet-4-5"
    assert s.soul_max_body_chars == 5000


# ── Validation (K2-03) ───────────────────────────────────────────────────────


@pytest.mark.parametrize("threshold", [-0.1, 1.1, 2.0])
def test_agreement_threshold_out_of_range_rejected(threshold: float) -> None:
    """kokoro_agreement_threshold must stay within [0, 1]."""
    with pytest.raises(ValidationError):
        Settings(kokoro_agreement_threshold=threshold)


@pytest.mark.parametrize("threshold", [0.0, 0.6, 1.0])
def test_agreement_threshold_bounds_accepted(threshold: float) -> None:
    """The inclusive bounds 0 and 1 are valid threshold values."""
    s = Settings(kokoro_agreement_threshold=threshold)
    assert s.kokoro_agreement_threshold == threshold


@pytest.mark.parametrize(
    "souls",
    [
        [],
        ["spirit"],
        ["spirit", "mind"],
        ["spirit", "mind", "heart", "risk"],
    ],
)
def test_kokoro_souls_must_have_exactly_three(souls: list[str]) -> None:
    """kokoro_souls must contain exactly three ids."""
    with pytest.raises(ValidationError):
        Settings(kokoro_souls=souls)


def test_kokoro_max_rounds_must_be_positive() -> None:
    """kokoro_max_rounds rejects zero and negatives."""
    with pytest.raises(ValidationError):
        Settings(kokoro_max_rounds=0)


def test_soul_max_body_chars_must_be_positive() -> None:
    """soul_max_body_chars rejects zero and negatives."""
    with pytest.raises(ValidationError):
        Settings(soul_max_body_chars=0)
