"""Tests for the guardrails-modernization config fields (Phase GRD — SPEC-GRD-CFG-001)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from prismal.core.config import Settings

# ── Defaults (SPEC-GRD-CFG-001) ──────────────────────────────────────────────


def test_guardrails_modernization_defaults_match_spec() -> None:
    s = Settings()
    assert s.nemo_classifier_enabled is False
    assert s.nemo_classifier_model is None
    assert s.nemo_classifier_categories == [
        "violence",
        "self_harm",
        "illegal_activities",
        "pii_request",
        "competitor_disparagement",
    ]
    assert s.nemo_classifier_threshold == 0.7
    assert s.nemo_classifier_timeout_seconds == 3.0
    assert s.structured_output_guard_enabled is False
    assert s.structured_output_guard_max_reasks == 2
    assert s.structured_output_guard_hub_validators_enabled is False


def test_guardrails_modernization_disabled_by_default_is_opt_in() -> None:
    assert Settings().nemo_classifier_enabled is False
    assert Settings().structured_output_guard_enabled is False


# ── Env parsing (PRISMAL_* prefix) ───────────────────────────────────────────


def test_guardrails_modernization_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRISMAL_NEMO_CLASSIFIER_ENABLED", "true")
    monkeypatch.setenv("PRISMAL_NEMO_CLASSIFIER_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("PRISMAL_NEMO_CLASSIFIER_CATEGORIES", '["violence", "self_harm"]')
    monkeypatch.setenv("PRISMAL_NEMO_CLASSIFIER_THRESHOLD", "0.5")
    monkeypatch.setenv("PRISMAL_NEMO_CLASSIFIER_TIMEOUT_SECONDS", "5.0")
    monkeypatch.setenv("PRISMAL_STRUCTURED_OUTPUT_GUARD_ENABLED", "true")
    monkeypatch.setenv("PRISMAL_STRUCTURED_OUTPUT_GUARD_MAX_REASKS", "4")
    monkeypatch.setenv("PRISMAL_STRUCTURED_OUTPUT_GUARD_HUB_VALIDATORS_ENABLED", "true")

    s = Settings()
    assert s.nemo_classifier_enabled is True
    assert s.nemo_classifier_model == "claude-haiku-4-5"
    assert s.nemo_classifier_categories == ["violence", "self_harm"]
    assert s.nemo_classifier_threshold == 0.5
    assert s.nemo_classifier_timeout_seconds == 5.0
    assert s.structured_output_guard_enabled is True
    assert s.structured_output_guard_max_reasks == 4
    assert s.structured_output_guard_hub_validators_enabled is True


# ── Validation (_validate_guardrails_modernization) ──────────────────────────


def test_threshold_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(nemo_classifier_threshold=1.5)
    with pytest.raises(ValidationError):
        Settings(nemo_classifier_threshold=-0.1)


def test_empty_categories_rejected_when_classifier_enabled() -> None:
    with pytest.raises(ValidationError):
        Settings(nemo_classifier_enabled=True, nemo_classifier_categories=[])


def test_empty_categories_allowed_when_classifier_disabled() -> None:
    s = Settings(nemo_classifier_enabled=False, nemo_classifier_categories=[])
    assert s.nemo_classifier_categories == []


def test_negative_max_reasks_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(structured_output_guard_max_reasks=-1)


def test_zero_max_reasks_accepted() -> None:
    assert Settings(structured_output_guard_max_reasks=0).structured_output_guard_max_reasks == 0
