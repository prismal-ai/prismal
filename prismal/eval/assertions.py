"""Assertion evaluators (SPEC-EVL-ASR-001).

Small, typed, composable checks over a captured :class:`Trajectory`. Pure
scoring functions never raise — a failure becomes ``passed=False`` with a
human-readable ``detail``. ``assert_semantic`` is async because the embeddings
port is async; ``assert_llm_judge``/``assert_groundedness`` are async because
they call the LLM-as-judge.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from prismal.eval.types import AssertionResult, AssertionType, Trajectory

if TYPE_CHECKING:
    from prismal.eval.types import Assertion, EvalCase

_GROUNDEDNESS_RUBRIC = (
    "Score how well the answer is supported by the retrieved context. "
    "1.0 = every claim is grounded in the context; 0.0 = unsupported/hallucinated."
)


def assert_exact(traj: Trajectory, a: Assertion) -> AssertionResult:
    """Pass iff the final answer exactly matches ``a.expected`` (whitespace-trimmed)."""
    expected = (a.expected or "").strip()
    actual = traj.final_answer.strip()
    passed = actual == expected
    return AssertionResult(
        assertion=a,
        passed=passed,
        score=1.0 if passed else 0.0,
        detail="" if passed else f"expected {expected!r}, got {actual!r}",
    )


def assert_tool_usage(traj: Trajectory, a: Assertion) -> AssertionResult:
    """Check ``must_call`` / ``never_call`` / ``max_steps`` over the trajectory."""
    used = _used_names(traj)
    failures: list[str] = []

    missing = [name for name in a.must_call if name not in used]
    if missing:
        failures.append(f"missing required: {missing}")

    forbidden = [name for name in a.never_call if name in used]
    if forbidden:
        failures.append(f"called forbidden: {forbidden}")

    if a.max_steps is not None and len(traj.steps) > a.max_steps:
        failures.append(f"steps {len(traj.steps)} > max {a.max_steps}")

    passed = not failures
    return AssertionResult(
        assertion=a,
        passed=passed,
        score=1.0 if passed else 0.0,
        detail="; ".join(failures),
    )


async def assert_semantic(traj: Trajectory, a: Assertion, *, embeddings: Any) -> AssertionResult:
    """Pass iff cosine(answer, expected) ≥ ``a.min_score`` via the embeddings port."""
    threshold = a.min_score if a.min_score is not None else 0.8
    answer_vec = await embeddings.aembed_query(traj.final_answer)
    expected_vec = await embeddings.aembed_query(a.expected or "")
    score = _cosine(answer_vec, expected_vec)
    passed = score >= threshold
    return AssertionResult(
        assertion=a,
        passed=passed,
        score=score,
        detail="" if passed else f"cosine {score:.3f} < {threshold}",
    )


async def assert_llm_judge(traj: Trajectory, a: Assertion, *, judge: Any) -> AssertionResult:
    """Pass iff the judge's rubric score ≥ ``a.min_score``."""
    threshold = a.min_score if a.min_score is not None else 0.7
    score = await judge.score(rubric=a.rubric or "", answer=traj.final_answer)
    passed = score >= threshold
    return AssertionResult(
        assertion=a,
        passed=passed,
        score=score,
        detail="" if passed else f"judge {score:.3f} < {threshold}",
    )


async def assert_groundedness(traj: Trajectory, a: Assertion, *, judge: Any) -> AssertionResult:
    """Pass iff the answer is grounded in the retrieved (tool) context."""
    threshold = a.min_score if a.min_score is not None else 0.8
    context = _retrieved_context(traj)
    score = await judge.score(
        rubric=_GROUNDEDNESS_RUBRIC, answer=traj.final_answer, context=context
    )
    passed = score >= threshold
    return AssertionResult(
        assertion=a,
        passed=passed,
        score=score,
        detail="" if passed else f"groundedness {score:.3f} < {threshold}",
    )


async def dispatch_assertions(
    traj: Trajectory,
    case: EvalCase,
    *,
    judge: Any = None,
    embeddings: Any = None,
) -> list[AssertionResult]:
    """Evaluate every assertion on *case* against *traj*.

    Routes each assertion to its evaluator. ``security`` assertions are handled
    by :mod:`prismal.eval.redteam` (V5); until then they yield an unsupported
    failure rather than silently passing.
    """
    results: list[AssertionResult] = []
    for a in case.assertions:
        if a.type is AssertionType.EXACT:
            results.append(assert_exact(traj, a))
        elif a.type is AssertionType.TOOL_USAGE:
            results.append(assert_tool_usage(traj, a))
        elif a.type is AssertionType.SEMANTIC:
            results.append(await assert_semantic(traj, a, embeddings=embeddings))
        elif a.type is AssertionType.LLM_JUDGE:
            results.append(await assert_llm_judge(traj, a, judge=judge))
        elif a.type is AssertionType.GROUNDEDNESS:
            results.append(await assert_groundedness(traj, a, judge=judge))
        else:  # SECURITY — implemented in V5
            results.append(_unsupported(a))
    return results


def _unsupported(a: Assertion) -> AssertionResult:
    """A placeholder failure for assertion types not yet wired here."""
    return AssertionResult(
        assertion=a,
        passed=False,
        score=0.0,
        detail=f"assertion type {a.type.value!r} not supported by dispatch_assertions",
    )


# ── helpers ──────────────────────────────────────────────────────────────────


def _used_names(traj: Trajectory) -> set[str]:
    """All agent/node and tool names exercised in the trajectory."""
    names = set(traj.visited_nodes)
    names.update(s.tool_name for s in traj.steps if s.tool_name)
    return names


def _retrieved_context(traj: Trajectory) -> str:
    """Concatenate tool-result content — the context surfaced to the answer."""
    return "\n".join(s.content for s in traj.steps if s.role == "tool" and s.content)


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two vectors; 0.0 for a zero vector or shape mismatch."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


__all__ = [
    "assert_exact",
    "assert_groundedness",
    "assert_llm_judge",
    "assert_semantic",
    "assert_tool_usage",
    "dispatch_assertions",
]
