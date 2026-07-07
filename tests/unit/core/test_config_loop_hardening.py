"""Tests for the loop-hardening config fields (Phase LH — SPEC-LH-CFG-001)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from prismal.core.config import Settings

# ── Defaults (SPEC-LH-CFG-001) ───────────────────────────────────────────────


def test_loop_hardening_defaults_match_spec() -> None:
    s = Settings()
    assert s.context_compaction_enabled is False
    assert s.context_compaction_strategy == "truncate"
    assert s.context_compaction_max_messages == 60
    assert s.context_compaction_token_threshold == 0
    assert s.context_compaction_keep_recent == 10
    assert s.context_compaction_summarizer_model is None
    assert s.context_compaction_min_interval_messages == 20
    assert s.tool_gating_enabled is False
    assert s.tool_gating_phase_map_path == "config/tool_gating_phases.yaml"


def test_loop_hardening_disabled_by_default_is_opt_in() -> None:
    assert Settings().context_compaction_enabled is False
    assert Settings().tool_gating_enabled is False


# ── Env parsing (PRISMAL_* prefix) ───────────────────────────────────────────


def test_loop_hardening_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRISMAL_CONTEXT_COMPACTION_ENABLED", "true")
    monkeypatch.setenv("PRISMAL_CONTEXT_COMPACTION_STRATEGY", "summarize")
    monkeypatch.setenv("PRISMAL_CONTEXT_COMPACTION_MAX_MESSAGES", "100")
    monkeypatch.setenv("PRISMAL_CONTEXT_COMPACTION_TOKEN_THRESHOLD", "5000")
    monkeypatch.setenv("PRISMAL_CONTEXT_COMPACTION_KEEP_RECENT", "20")
    monkeypatch.setenv("PRISMAL_CONTEXT_COMPACTION_SUMMARIZER_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("PRISMAL_CONTEXT_COMPACTION_MIN_INTERVAL_MESSAGES", "30")
    monkeypatch.setenv("PRISMAL_TOOL_GATING_ENABLED", "true")
    monkeypatch.setenv("PRISMAL_TOOL_GATING_PHASE_MAP_PATH", "/etc/phases.yaml")

    s = Settings()
    assert s.context_compaction_enabled is True
    assert s.context_compaction_strategy == "summarize"
    assert s.context_compaction_max_messages == 100
    assert s.context_compaction_token_threshold == 5000
    assert s.context_compaction_keep_recent == 20
    assert s.context_compaction_summarizer_model == "claude-haiku-4-5"
    assert s.context_compaction_min_interval_messages == 30
    assert s.tool_gating_enabled is True
    assert s.tool_gating_phase_map_path == "/etc/phases.yaml"


# ── Validation (_validate_loop_hardening) ────────────────────────────────────


def test_invalid_strategy_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(context_compaction_strategy="delete_everything")


def test_valid_strategies_accepted() -> None:
    for strategy in ("truncate", "summarize"):
        assert (
            Settings(context_compaction_strategy=strategy).context_compaction_strategy == strategy
        )


def test_negative_max_messages_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(context_compaction_max_messages=-1)


def test_negative_keep_recent_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(context_compaction_keep_recent=-1)


def test_negative_token_threshold_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(context_compaction_token_threshold=-1)


def test_zero_token_threshold_accepted() -> None:
    assert Settings(context_compaction_token_threshold=0).context_compaction_token_threshold == 0
