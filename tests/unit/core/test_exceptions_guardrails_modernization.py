"""Tests for the guardrails-modernization exception hierarchy (Phase GRD — SPEC-GRD-ERR-001)."""

from __future__ import annotations

import pytest

from prismal.core.exceptions import (
    GuardrailsModernizationError,
    MissingDependencyError,
    NemoClassifierConfigError,
    NemoClassifierError,
    PrismalError,
    StructuredOutputGuardError,
    StructuredOutputReaskExhausted,
)


def test_guardrails_modernization_error_is_prismal_error() -> None:
    assert issubclass(GuardrailsModernizationError, PrismalError)


@pytest.mark.parametrize(
    "exc",
    [
        NemoClassifierError,
        NemoClassifierConfigError,
        StructuredOutputGuardError,
        StructuredOutputReaskExhausted,
    ],
)
def test_subclasses_extend_guardrails_modernization_error(
    exc: type[GuardrailsModernizationError],
) -> None:
    assert issubclass(exc, GuardrailsModernizationError)


def test_nemo_classifier_config_error_extends_nemo_classifier_error() -> None:
    assert issubclass(NemoClassifierConfigError, NemoClassifierError)


def test_structured_output_reask_exhausted_extends_structured_output_guard_error() -> None:
    assert issubclass(StructuredOutputReaskExhausted, StructuredOutputGuardError)


def test_nemo_classifier_error_is_raisable_as_prismal_error() -> None:
    with pytest.raises(PrismalError):
        raise NemoClassifierError("classifier failed")


def test_missing_dependency_error_reused_not_reinvented() -> None:
    """GRD2 reuses the existing MissingDependencyError, no new class is invented."""
    with pytest.raises(MissingDependencyError) as exc_info:
        raise MissingDependencyError(
            "guardrails-ai is not installed", extra_to_install="guardrails-ai"
        )
    assert "guardrails-ai" in str(exc_info.value)
