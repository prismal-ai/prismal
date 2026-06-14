"""Tests for the runtime-hardening exception hierarchy (Phase H — SPEC-HRD-ERR-001)."""

from __future__ import annotations

import pytest

from prismal.core.exceptions import (
    HardeningConfigError,
    HardeningError,
    IndirectInjectionBlocked,
    OutputValidationError,
    PrismalError,
    RunawayStopped,
    ToolPolicyDenied,
)


def test_hardening_error_is_prismal_error() -> None:
    assert issubclass(HardeningError, PrismalError)


@pytest.mark.parametrize(
    "exc",
    [
        IndirectInjectionBlocked,
        OutputValidationError,
        ToolPolicyDenied,
        RunawayStopped,
        HardeningConfigError,
    ],
)
def test_subclasses_extend_hardening_error(exc: type[HardeningError]) -> None:
    assert issubclass(exc, HardeningError)


def test_indirect_injection_blocked_is_raisable_as_hardening_error() -> None:
    with pytest.raises(HardeningError):
        raise IndirectInjectionBlocked("injection in rag content")


def test_tool_policy_denied_is_raisable_as_prismal_error() -> None:
    with pytest.raises(PrismalError):
        raise ToolPolicyDenied("denied")
