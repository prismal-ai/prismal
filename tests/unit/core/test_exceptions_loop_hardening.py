"""Tests for the loop-hardening exception hierarchy (Phase LH — SPEC-LH-ERR-001)."""

from __future__ import annotations

import pytest

from prismal.core.exceptions import (
    ContextCompactionError,
    LoopHardeningError,
    PrismalError,
    ToolGatingConfigError,
)


def test_loop_hardening_error_is_prismal_error() -> None:
    assert issubclass(LoopHardeningError, PrismalError)


@pytest.mark.parametrize("exc", [ContextCompactionError, ToolGatingConfigError])
def test_subclasses_extend_loop_hardening_error(exc: type[LoopHardeningError]) -> None:
    assert issubclass(exc, LoopHardeningError)


def test_tool_gating_config_error_is_raisable_as_prismal_error() -> None:
    with pytest.raises(PrismalError):
        raise ToolGatingConfigError("bad phase map")
