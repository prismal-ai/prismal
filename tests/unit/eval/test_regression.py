"""Tests for the regression gate (Phase V — SPEC-EVL-REG-001 / RF-EVL-004).

``compare`` fails if pass_rate drops below tolerance, or if avg_steps /
tool_error_rate / avg_cost_usd rise beyond tolerance vs the baseline.
"""

from __future__ import annotations

import dataclasses

from prismal.eval.regression import RegressionResult, compare
from prismal.eval.types import Scorecard


def _card(
    *,
    pass_rate: float = 1.0,
    avg_steps: float = 2.0,
    tool_error_rate: float = 0.0,
    avg_cost_usd: float = 0.0,
) -> Scorecard:
    return Scorecard(
        suite="s",
        version="3.3.0",
        pass_rate=pass_rate,
        avg_steps=avg_steps,
        tool_error_rate=tool_error_rate,
        avg_cost_usd=avg_cost_usd,
        avg_latency_ms=0.0,
        cases=[],
    )


def test_identical_scorecards_pass() -> None:
    base = _card()
    res = compare(_card(), base)
    assert res.passed is True
    assert res.regressions == []


def test_result_is_frozen() -> None:
    res = RegressionResult(passed=True, regressions=[])
    with __import__("pytest").raises(dataclasses.FrozenInstanceError):
        res.passed = False  # type: ignore[misc]


def test_pass_rate_drop_beyond_tolerance_fails() -> None:
    base = _card(pass_rate=1.0)
    cur = _card(pass_rate=0.9)
    res = compare(cur, base, tolerance=0.02)
    assert res.passed is False
    assert any("pass_rate" in r for r in res.regressions)


def test_pass_rate_drop_within_tolerance_passes() -> None:
    base = _card(pass_rate=1.0)
    cur = _card(pass_rate=0.99)
    assert compare(cur, base, tolerance=0.02).passed is True


def test_avg_steps_rise_beyond_relative_tolerance_fails() -> None:
    base = _card(avg_steps=2.0)
    cur = _card(avg_steps=2.5)  # +25% > 2% tolerance
    res = compare(cur, base, tolerance=0.02)
    assert res.passed is False
    assert any("avg_steps" in r for r in res.regressions)


def test_tool_error_rate_rise_from_zero_fails() -> None:
    base = _card(tool_error_rate=0.0)
    cur = _card(tool_error_rate=0.1)
    res = compare(cur, base, tolerance=0.02)
    assert res.passed is False
    assert any("tool_error_rate" in r for r in res.regressions)


def test_cost_rise_within_tolerance_passes() -> None:
    base = _card(avg_cost_usd=1.0)
    cur = _card(avg_cost_usd=1.01)  # +1% < 2% tolerance
    assert compare(cur, base, tolerance=0.02).passed is True


def test_improvement_never_regresses() -> None:
    base = _card(pass_rate=0.8, avg_steps=5.0, tool_error_rate=0.2, avg_cost_usd=2.0)
    cur = _card(pass_rate=1.0, avg_steps=2.0, tool_error_rate=0.0, avg_cost_usd=0.5)
    res = compare(cur, base)
    assert res.passed is True
    assert res.regressions == []
