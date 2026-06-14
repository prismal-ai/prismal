"""Tests for the runtime-hardening config fields (Phase H — SPEC-HRD-CFG-001)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from prismal.core.config import Settings

# ── Defaults (SPEC-HRD-CFG-001) ──────────────────────────────────────────────


def test_hardening_defaults_match_spec() -> None:
    s = Settings()
    assert s.hardening_enabled is False
    assert s.hardening_mode == "warn"
    assert s.taint_tracking_enabled is True
    assert s.hardening_injection_threshold == 0.7
    assert s.hardening_injection_classifier is False
    assert s.output_validation_enabled is True
    assert s.tool_policy_path == "config/tool_policies.yaml"
    assert s.hardening_tool_policy_default == "allow"
    assert s.hardening_runaway_max_steps == 40
    assert s.hardening_runaway_stagnation_window == 4
    assert s.hardening_pii_output is False


def test_hardening_disabled_by_default_is_opt_in() -> None:
    assert Settings().hardening_enabled is False


# ── Env parsing (PRISMAL_* prefix) ───────────────────────────────────────────


def test_hardening_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRISMAL_HARDENING_ENABLED", "true")
    monkeypatch.setenv("PRISMAL_HARDENING_MODE", "enforce")
    monkeypatch.setenv("PRISMAL_TAINT_TRACKING_ENABLED", "false")
    monkeypatch.setenv("PRISMAL_HARDENING_INJECTION_THRESHOLD", "0.5")
    monkeypatch.setenv("PRISMAL_HARDENING_INJECTION_CLASSIFIER", "true")
    monkeypatch.setenv("PRISMAL_OUTPUT_VALIDATION_ENABLED", "false")
    monkeypatch.setenv("PRISMAL_TOOL_POLICY_PATH", "/etc/policies.yaml")
    monkeypatch.setenv("PRISMAL_HARDENING_TOOL_POLICY_DEFAULT", "deny")
    monkeypatch.setenv("PRISMAL_HARDENING_RUNAWAY_MAX_STEPS", "10")
    monkeypatch.setenv("PRISMAL_HARDENING_RUNAWAY_STAGNATION_WINDOW", "3")
    monkeypatch.setenv("PRISMAL_HARDENING_PII_OUTPUT", "true")

    s = Settings()
    assert s.hardening_enabled is True
    assert s.hardening_mode == "enforce"
    assert s.taint_tracking_enabled is False
    assert s.hardening_injection_threshold == 0.5
    assert s.hardening_injection_classifier is True
    assert s.output_validation_enabled is False
    assert s.tool_policy_path == "/etc/policies.yaml"
    assert s.hardening_tool_policy_default == "deny"
    assert s.hardening_runaway_max_steps == 10
    assert s.hardening_runaway_stagnation_window == 3
    assert s.hardening_pii_output is True


# ── Validation (_validate_hardening) ─────────────────────────────────────────


def test_threshold_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(hardening_injection_threshold=1.5)
    with pytest.raises(ValidationError):
        Settings(hardening_injection_threshold=-0.1)


def test_negative_runaway_steps_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(hardening_runaway_max_steps=-1)
    with pytest.raises(ValidationError):
        Settings(hardening_runaway_stagnation_window=-1)


def test_invalid_mode_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(hardening_mode="paranoid")


def test_valid_modes_accepted() -> None:
    for mode in ("off", "warn", "enforce"):
        assert Settings(hardening_mode=mode).hardening_mode == mode


def test_invalid_tool_policy_default_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(hardening_tool_policy_default="maybe")


def test_valid_tool_policy_defaults_accepted() -> None:
    for default in ("allow", "deny"):
        assert (
            Settings(hardening_tool_policy_default=default).hardening_tool_policy_default == default
        )
