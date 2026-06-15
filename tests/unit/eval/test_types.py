"""Tests for the eval-harness value objects (Phase V — SPEC-EVL-TYP-001).

V1 "done when": value objects construct and are frozen; defaults match the
SPEC; ``AssertionType`` enumerates the six assertion kinds.
"""

from __future__ import annotations

import dataclasses

import pytest

from prismal.eval.types import (
    Assertion,
    AssertionResult,
    AssertionType,
    CaseResult,
    EvalCase,
    EvalSet,
    Scorecard,
    Trajectory,
    TrajectoryStep,
)

# ── AssertionType ─────────────────────────────────────────────────────────────


def test_assertion_type_members() -> None:
    """The six assertion kinds from the SPEC are present as str-enum values."""
    assert AssertionType.EXACT == "exact"
    assert AssertionType.SEMANTIC == "semantic"
    assert AssertionType.LLM_JUDGE == "llm_judge"
    assert AssertionType.TOOL_USAGE == "tool_usage"
    assert AssertionType.GROUNDEDNESS == "groundedness"
    assert AssertionType.SECURITY == "security"


# ── Assertion ─────────────────────────────────────────────────────────────────


def test_assertion_defaults() -> None:
    """An Assertion needs only its type; every other field defaults."""
    a = Assertion(type=AssertionType.EXACT)
    assert a.expected is None
    assert a.min_score is None
    assert a.rubric is None
    assert a.must_call == []
    assert a.never_call == []
    assert a.max_steps is None
    assert a.attack_class is None
    assert a.must_block is True


def test_assertion_is_frozen() -> None:
    """Assertions are immutable value objects."""
    a = Assertion(type=AssertionType.EXACT, expected="hi")
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.expected = "bye"  # type: ignore[misc]


def test_assertion_mutable_defaults_are_independent() -> None:
    """The list defaults are per-instance, not shared."""
    a = Assertion(type=AssertionType.TOOL_USAGE)
    b = Assertion(type=AssertionType.TOOL_USAGE)
    a.must_call.append("rag_agent")
    assert b.must_call == []


# ── EvalCase / EvalSet ────────────────────────────────────────────────────────


def test_eval_case_defaults() -> None:
    """An EvalCase carries id/input/assertions; setup and tags default empty."""
    case = EvalCase(
        id="c1",
        input="hello",
        assertions=[Assertion(type=AssertionType.EXACT, expected="hi")],
    )
    assert case.setup == {}
    assert case.tags == []
    assert case.assertions[0].expected == "hi"


def test_eval_set_holds_cases() -> None:
    """An EvalSet groups a suite name and its cases."""
    es = EvalSet(suite="smoke", cases=[EvalCase(id="c1", input="x", assertions=[])])
    assert es.suite == "smoke"
    assert es.cases[0].id == "c1"


# ── Trajectory ────────────────────────────────────────────────────────────────


def test_trajectory_step_defaults() -> None:
    """A TrajectoryStep needs node+role; content/tool fields default."""
    step = TrajectoryStep(node="supervisor", role="assistant")
    assert step.content == ""
    assert step.tool_name is None
    assert step.tool_args is None
    assert step.tool_ok is None


def test_trajectory_construct() -> None:
    """A Trajectory aggregates the per-case metrics."""
    traj = Trajectory(
        case_id="c1",
        final_answer="42",
        steps=[TrajectoryStep(node="supervisor", role="assistant", content="42")],
        visited_nodes=["supervisor"],
        tool_calls=0,
        tool_errors=0,
        cost_usd=0.0,
        tokens=0,
        latency_ms=1.0,
        terminated=True,
    )
    assert traj.final_answer == "42"
    assert traj.terminated is True


# ── Results / Scorecard ───────────────────────────────────────────────────────


def test_case_result_and_scorecard() -> None:
    """CaseResult wraps a trajectory + assertion results; Scorecard aggregates."""
    traj = Trajectory(
        case_id="c1",
        final_answer="ok",
        steps=[],
        visited_nodes=[],
        tool_calls=0,
        tool_errors=0,
        cost_usd=0.0,
        tokens=0,
        latency_ms=0.0,
        terminated=True,
    )
    ar = AssertionResult(
        assertion=Assertion(type=AssertionType.EXACT, expected="ok"),
        passed=True,
        score=1.0,
    )
    cr = CaseResult(case_id="c1", passed=True, trajectory=traj, assertion_results=[ar])
    card = Scorecard(
        suite="smoke",
        version="3.3.0",
        pass_rate=1.0,
        avg_steps=0.0,
        tool_error_rate=0.0,
        avg_cost_usd=0.0,
        avg_latency_ms=0.0,
        cases=[cr],
    )
    assert card.pass_rate == 1.0
    assert card.cases[0].passed is True
    assert card.cases[0].assertion_results[0].score == 1.0
