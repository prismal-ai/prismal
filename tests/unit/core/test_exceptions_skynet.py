"""Tests for the Skynet exception hierarchy (Fase S — SPEC-SKY-ERR-001)."""

from __future__ import annotations

import pytest

import prismal.core.exceptions as exceptions_module
from prismal.core.exceptions import (
    PrismalError,
    SkynetBudgetExceeded,
    SkynetConfigError,
    SkynetError,
    SkynetPlanError,
    SwarmWorkerError,
)

ALL_SKYNET_EXCEPTIONS = [
    SkynetError,
    SkynetPlanError,
    SwarmWorkerError,
    SkynetConfigError,
    SkynetBudgetExceeded,
]


def test_skynet_error_is_prismal_error() -> None:
    """The Skynet base error joins the PrismalError hierarchy."""
    assert issubclass(SkynetError, PrismalError)


@pytest.mark.parametrize("exc", ALL_SKYNET_EXCEPTIONS)
def test_skynet_exceptions_subclass_skynet_error(exc: type[Exception]) -> None:
    """Every Skynet exception is catchable via SkynetError."""
    assert issubclass(exc, SkynetError)


@pytest.mark.parametrize("exc", ALL_SKYNET_EXCEPTIONS)
def test_skynet_exceptions_exported(exc: type[Exception]) -> None:
    """Every Skynet exception is listed in __all__."""
    assert exc.__name__ in exceptions_module.__all__


def test_skynet_exceptions_carry_message() -> None:
    """Skynet exceptions behave like plain exceptions (message round-trip)."""
    err = SkynetPlanError("planner failed")
    assert str(err) == "planner failed"
    with pytest.raises(SkynetError, match="planner failed"):
        raise err
