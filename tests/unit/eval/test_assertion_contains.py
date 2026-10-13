"""Tests for the deterministic ``contains`` assertion (TKE9-04).

The long-loop A/B needs a solve rate that catches a wrong answer: a run that
calls the tool once and then fabricates a record must fail. ``contains``
passes iff every ``must_contain`` value appears in the final answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prismal.core.exceptions import EvalSetError
from prismal.eval.assertions import assert_contains, dispatch_assertions
from prismal.eval.types import Assertion, AssertionType, EvalCase, EvalSet, Trajectory


def _traj(final: str) -> Trajectory:
    return Trajectory(
        case_id="c",
        final_answer=final,
        steps=[],
        visited_nodes=[],
        tool_calls=0,
        tool_errors=0,
        cost_usd=0.0,
        tokens=0,
        latency_ms=0.0,
        terminated=True,
    )


def _contains(*values: str) -> Assertion:
    return Assertion(type=AssertionType.CONTAINS, must_contain=list(values))


def test_passes_when_every_value_is_present() -> None:
    result = assert_contains(
        _traj("ORD-007: 655, ORD-008: 719, ORD-009: 702"), _contains("655", "719", "702")
    )
    assert result.passed is True
    assert result.score == 1.0


def test_fails_and_names_the_missing_values() -> None:
    result = assert_contains(_traj("ORD-007 total is 655"), _contains("655", "719"))
    assert result.passed is False
    assert result.score == 0.5
    assert "719" in result.detail


def test_thousands_separators_between_digits_are_ignored() -> None:
    assert assert_contains(_traj("The sum is 2,712 USD."), _contains("2712")).passed is True


def test_match_is_case_insensitive() -> None:
    assert assert_contains(_traj("Status: DELIVERED"), _contains("delivered")).passed is True


async def test_dispatch_routes_contains() -> None:
    case = EvalCase(id="c", input="q", assertions=[_contains("719")])
    [result] = await dispatch_assertions(_traj("719"), case)
    assert result.passed is True


def test_yaml_parses_must_contain(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(
        "suite: s\ncases:\n  - id: a\n    input: q\n    assertions:\n"
        "      - type: contains\n        must_contain: [655, '719']\n",
        encoding="utf-8",
    )
    [assertion] = EvalSet.from_yaml(path).cases[0].assertions
    assert assertion.type is AssertionType.CONTAINS
    assert assertion.must_contain == ["655", "719"]


def test_yaml_rejects_contains_without_values(tmp_path: Path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(
        "suite: s\ncases:\n  - id: a\n    input: q\n    assertions:\n      - type: contains\n",
        encoding="utf-8",
    )
    with pytest.raises(EvalSetError):
        EvalSet.from_yaml(path)
