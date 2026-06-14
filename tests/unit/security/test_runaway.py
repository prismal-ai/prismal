"""Tests for the runaway guard (Phase H — SPEC-HRD-RUN-001)."""

from __future__ import annotations

from prismal.core.config import Settings
from prismal.security.runaway import RunawayGuard, RunawayStatus


def _guard(**overrides: object) -> RunawayGuard:
    return RunawayGuard(settings=Settings(hardening_enabled=True, **overrides))


def test_tick_increments_step() -> None:
    guard = _guard(hardening_runaway_max_steps=0, hardening_runaway_stagnation_window=0)
    s1 = guard.tick(node="coder", signature="a")
    s2 = guard.tick(node="coder", signature="b")
    assert isinstance(s1, RunawayStatus)
    assert s1.step == 1
    assert s2.step == 2
    assert s1.stop is False


def test_step_cap_stops() -> None:
    guard = _guard(hardening_runaway_max_steps=3, hardening_runaway_stagnation_window=0)
    for sig in ("a", "b", "c"):
        assert guard.tick(node="n", signature=sig).stop is False
    status = guard.tick(node="n", signature="d")  # 4th step exceeds cap of 3
    assert status.stop is True
    assert status.reason == "step_cap"


def test_stagnation_stops_on_repeated_signature() -> None:
    guard = _guard(hardening_runaway_max_steps=0, hardening_runaway_stagnation_window=3)
    assert guard.tick(node="n", signature="same").stop is False
    assert guard.tick(node="n", signature="same").stop is False
    status = guard.tick(node="n", signature="same")  # 3 identical in a row
    assert status.stop is True
    assert status.reason == "stagnation"


def test_distinct_signatures_do_not_stagnate() -> None:
    guard = _guard(hardening_runaway_max_steps=0, hardening_runaway_stagnation_window=3)
    for sig in ("a", "b", "a", "b", "a", "b"):
        assert guard.tick(node="n", signature=sig).stop is False


def test_step_cap_zero_is_unlimited() -> None:
    guard = _guard(hardening_runaway_max_steps=0, hardening_runaway_stagnation_window=0)
    for _ in range(100):
        assert guard.tick(node="n", signature="x").stop is False


def test_stagnation_window_zero_disabled() -> None:
    guard = _guard(hardening_runaway_max_steps=0, hardening_runaway_stagnation_window=0)
    for _ in range(10):
        assert guard.tick(node="n", signature="same").stop is False
