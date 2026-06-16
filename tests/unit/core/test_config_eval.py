"""Tests for the eval-harness config fields (Phase V — SPEC-EVL-CFG-001)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from prismal.core.config import Settings

# ── Defaults (SPEC-EVL-CFG-001) ──────────────────────────────────────────────


def test_eval_defaults_match_spec() -> None:
    s = Settings()
    assert s.eval_default_mode == "fakes"
    assert s.eval_judge_model == ""
    assert s.eval_regression_tolerance == 0.02
    assert s.eval_seed == 0
    assert s.eval_langfuse_export is False


# ── Env parsing (PRISMAL_* prefix) ───────────────────────────────────────────


def test_eval_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRISMAL_EVAL_DEFAULT_MODE", "live_api")
    monkeypatch.setenv("PRISMAL_EVAL_JUDGE_MODEL", "claude-opus-4-8")
    monkeypatch.setenv("PRISMAL_EVAL_REGRESSION_TOLERANCE", "0.05")
    monkeypatch.setenv("PRISMAL_EVAL_SEED", "7")
    monkeypatch.setenv("PRISMAL_EVAL_LANGFUSE_EXPORT", "true")

    s = Settings()
    assert s.eval_default_mode == "live_api"
    assert s.eval_judge_model == "claude-opus-4-8"
    assert s.eval_regression_tolerance == 0.05
    assert s.eval_seed == 7
    assert s.eval_langfuse_export is True


# ── Validation ───────────────────────────────────────────────────────────────


def test_eval_unknown_mode_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(eval_default_mode="psychic")


def test_eval_negative_tolerance_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(eval_regression_tolerance=-0.1)
