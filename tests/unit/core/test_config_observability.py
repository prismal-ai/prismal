"""Tests for the observability config fields (Phase OBS — SPEC-OBS-CFG-001)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from prismal.core.config import Settings

# ── Defaults (SPEC-OBS-CFG-001) ──────────────────────────────────────────────


def test_observability_defaults_match_spec() -> None:
    s = Settings()
    assert s.observability_enabled is False
    assert s.observability_run_buffer_size == 200
    assert s.observability_max_runs == 500
    assert s.observability_score_source_default == "system"
    assert s.observability_dataset_export_format == "langsmith"


def test_observability_disabled_by_default_is_opt_in() -> None:
    assert Settings().observability_enabled is False


# ── Env parsing (PRISMAL_* prefix) ───────────────────────────────────────────


def test_observability_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRISMAL_OBSERVABILITY_ENABLED", "true")
    monkeypatch.setenv("PRISMAL_OBSERVABILITY_RUN_BUFFER_SIZE", "50")
    monkeypatch.setenv("PRISMAL_OBSERVABILITY_MAX_RUNS", "100")
    monkeypatch.setenv("PRISMAL_OBSERVABILITY_SCORE_SOURCE_DEFAULT", "human")
    monkeypatch.setenv("PRISMAL_OBSERVABILITY_DATASET_EXPORT_FORMAT", "langfuse")

    s = Settings()
    assert s.observability_enabled is True
    assert s.observability_run_buffer_size == 50
    assert s.observability_max_runs == 100
    assert s.observability_score_source_default == "human"
    assert s.observability_dataset_export_format == "langfuse"


# ── Validation (_validate_observability) ─────────────────────────────────────


def test_non_positive_buffer_sizes_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(observability_run_buffer_size=0)
    with pytest.raises(ValidationError):
        Settings(observability_max_runs=0)


def test_invalid_export_format_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(observability_dataset_export_format="parquet")


def test_valid_export_formats_accepted() -> None:
    for fmt in ("langsmith", "langfuse"):
        assert (
            Settings(observability_dataset_export_format=fmt).observability_dataset_export_format
            == fmt
        )


def test_invalid_score_source_default_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(observability_score_source_default="robot")


def test_valid_score_sources_accepted() -> None:
    for src in ("human", "llm_judge", "system"):
        assert (
            Settings(observability_score_source_default=src).observability_score_source_default
            == src
        )
