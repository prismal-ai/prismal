"""Unit tests for the observability exception hierarchy (OBS1-02 — SPEC-OBS-ERR-001)."""

from __future__ import annotations

from prismal.core.exceptions import (
    ObservabilityConfigError,
    ObservabilityError,
    PrismalError,
    RunNotFoundError,
)


def test_observability_error_is_prismal_error() -> None:
    assert issubclass(ObservabilityError, PrismalError)


def test_config_error_is_observability_error() -> None:
    assert issubclass(ObservabilityConfigError, ObservabilityError)


def test_run_not_found_is_observability_error() -> None:
    assert issubclass(RunNotFoundError, ObservabilityError)


def test_raisable_and_catchable_as_base() -> None:
    for exc_cls in (ObservabilityConfigError, RunNotFoundError):
        try:
            raise exc_cls("boom")
        except ObservabilityError as exc:
            assert str(exc) == "boom"
