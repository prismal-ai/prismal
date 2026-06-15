"""Tests for scorecard rendering (Phase V — SPEC-EVL-RPT-001 / RF-EVL-004)."""

from __future__ import annotations

import json

from prismal.eval.report import to_json, to_langfuse, to_markdown
from prismal.eval.types import (
    Assertion,
    AssertionResult,
    AssertionType,
    CaseResult,
    Scorecard,
    Trajectory,
)


def _card() -> Scorecard:
    traj = Trajectory(
        case_id="c1",
        final_answer="ok",
        steps=[],
        visited_nodes=["supervisor"],
        tool_calls=1,
        tool_errors=0,
        cost_usd=0.001,
        tokens=12,
        latency_ms=5.0,
        terminated=True,
    )
    ar = AssertionResult(
        assertion=Assertion(type=AssertionType.EXACT, expected="ok"),
        passed=True,
        score=1.0,
    )
    cr = CaseResult(case_id="c1", passed=True, trajectory=traj, assertion_results=[ar])
    return Scorecard(
        suite="smoke",
        version="3.3.0",
        pass_rate=1.0,
        avg_steps=0.0,
        tool_error_rate=0.0,
        avg_cost_usd=0.001,
        avg_latency_ms=5.0,
        cases=[cr],
    )


def test_to_json_round_trips_metrics() -> None:
    data = json.loads(to_json(_card()))
    assert data["suite"] == "smoke"
    assert data["version"] == "3.3.0"
    assert data["pass_rate"] == 1.0
    assert len(data["cases"]) == 1
    assert data["cases"][0]["case_id"] == "c1"
    # Enum assertion type serialises as its string value.
    assert data["cases"][0]["assertion_results"][0]["assertion"]["type"] == "exact"


def test_to_markdown_contains_suite_and_metrics() -> None:
    md = to_markdown(_card())
    assert "smoke" in md
    assert "3.3.0" in md
    assert "c1" in md
    # A header and a metrics mention.
    assert md.lstrip().startswith("#")
    assert "Pass rate" in md or "pass_rate" in md


def test_to_langfuse_noop_when_disabled() -> None:
    """With no settings / export disabled, to_langfuse is a safe no-op."""
    to_langfuse(_card())  # must not raise
