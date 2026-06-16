"""Tests for assertion evaluators (Phase V — SPEC-EVL-ASR-001 / RF-EVL-003).

Each assertion type passes and fails on crafted trajectories. Semantic uses an
injected embeddings double; llm_judge/groundedness use an injected judge double.
``assert_semantic`` is async because the embeddings port is async (a deviation
from the SPEC's sync signature, justified by ``EmbeddingsPort.aembed_query``).
"""

from __future__ import annotations

from prismal.eval.assertions import (
    assert_exact,
    assert_groundedness,
    assert_llm_judge,
    assert_semantic,
    assert_tool_usage,
)
from prismal.eval.types import Assertion, AssertionType, Trajectory, TrajectoryStep


def _traj(
    *,
    final: str = "",
    steps: list[TrajectoryStep] | None = None,
    visited: list[str] | None = None,
) -> Trajectory:
    return Trajectory(
        case_id="c",
        final_answer=final,
        steps=steps or [],
        visited_nodes=visited or [],
        tool_calls=0,
        tool_errors=0,
        cost_usd=0.0,
        tokens=0,
        latency_ms=0.0,
        terminated=True,
    )


class _FakeEmbeddings:
    """Maps known texts to fixed vectors; everything else to an orthogonal one."""

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self._vectors = vectors

    async def aembed_query(self, text: str) -> list[float]:
        return self._vectors.get(text, [0.0, 1.0])


class _FakeJudge:
    """Records its last call and returns a fixed score."""

    def __init__(self, score: float) -> None:
        self._score = score
        self.calls: list[dict[str, str]] = []

    async def score(self, *, rubric: str, answer: str, context: str = "") -> float:
        self.calls.append({"rubric": rubric, "answer": answer, "context": context})
        return self._score


# ── exact ─────────────────────────────────────────────────────────────────────


def test_assert_exact_passes_on_match() -> None:
    a = Assertion(type=AssertionType.EXACT, expected="42")
    res = assert_exact(_traj(final="42"), a)
    assert res.passed is True
    assert res.score == 1.0


def test_assert_exact_fails_on_mismatch() -> None:
    a = Assertion(type=AssertionType.EXACT, expected="42")
    res = assert_exact(_traj(final="43"), a)
    assert res.passed is False
    assert res.score == 0.0


# ── tool_usage ──────────────────────────────────────────────────────────────


def test_tool_usage_must_call_passes_when_present() -> None:
    traj = _traj(
        visited=["supervisor", "rag_agent"],
        steps=[TrajectoryStep(node="rag_agent", role="assistant", tool_name="rag_search")],
    )
    a = Assertion(type=AssertionType.TOOL_USAGE, must_call=["rag_agent"])
    assert assert_tool_usage(traj, a).passed is True


def test_tool_usage_must_call_fails_when_absent() -> None:
    traj = _traj(visited=["supervisor", "coder"])
    a = Assertion(type=AssertionType.TOOL_USAGE, must_call=["rag_agent"])
    assert assert_tool_usage(traj, a).passed is False


def test_tool_usage_never_call_fails_when_forbidden_tool_used() -> None:
    traj = _traj(steps=[TrajectoryStep(node="coder", role="assistant", tool_name="delete_file")])
    a = Assertion(type=AssertionType.TOOL_USAGE, never_call=["delete_file"])
    assert assert_tool_usage(traj, a).passed is False


def test_tool_usage_max_steps_fails_when_exceeded() -> None:
    steps = [TrajectoryStep(node="coder", role="assistant", content=str(i)) for i in range(8)]
    traj = _traj(steps=steps)
    a = Assertion(type=AssertionType.TOOL_USAGE, max_steps=6)
    assert assert_tool_usage(traj, a).passed is False


# ── semantic ──────────────────────────────────────────────────────────────────


async def test_assert_semantic_passes_above_threshold() -> None:
    emb = _FakeEmbeddings({"hello world": [1.0, 0.0], "hi world": [1.0, 0.0]})
    a = Assertion(type=AssertionType.SEMANTIC, expected="hi world", min_score=0.9)
    res = await assert_semantic(_traj(final="hello world"), a, embeddings=emb)
    assert res.passed is True
    assert res.score is not None and res.score >= 0.9


async def test_assert_semantic_fails_below_threshold() -> None:
    emb = _FakeEmbeddings({"hello world": [1.0, 0.0], "unrelated": [0.0, 1.0]})
    a = Assertion(type=AssertionType.SEMANTIC, expected="unrelated", min_score=0.9)
    res = await assert_semantic(_traj(final="hello world"), a, embeddings=emb)
    assert res.passed is False


# ── llm_judge ─────────────────────────────────────────────────────────────────


async def test_assert_llm_judge_passes_above_threshold() -> None:
    judge = _FakeJudge(0.85)
    a = Assertion(type=AssertionType.LLM_JUDGE, rubric="Cites the budget layer", min_score=0.7)
    res = await assert_llm_judge(_traj(final="The budget layer caps cost."), a, judge=judge)
    assert res.passed is True
    assert res.score == 0.85
    assert judge.calls[0]["rubric"] == "Cites the budget layer"
    assert judge.calls[0]["answer"] == "The budget layer caps cost."


async def test_assert_llm_judge_fails_below_threshold() -> None:
    judge = _FakeJudge(0.4)
    a = Assertion(type=AssertionType.LLM_JUDGE, rubric="r", min_score=0.7)
    res = await assert_llm_judge(_traj(final="x"), a, judge=judge)
    assert res.passed is False


# ── groundedness ──────────────────────────────────────────────────────────────


async def test_assert_groundedness_uses_tool_context() -> None:
    judge = _FakeJudge(0.9)
    traj = _traj(
        final="The cap aborts the run.",
        steps=[
            TrajectoryStep(node="rag_agent", role="tool", content="hard cap aborts the run"),
            TrajectoryStep(node="rag_agent", role="assistant", content="The cap aborts the run."),
        ],
    )
    a = Assertion(type=AssertionType.GROUNDEDNESS, min_score=0.8)
    res = await assert_groundedness(traj, a, judge=judge)
    assert res.passed is True
    # The retrieved (tool) content was passed as context to the judge.
    assert "hard cap aborts the run" in judge.calls[0]["context"]


async def test_assert_groundedness_fails_below_threshold() -> None:
    judge = _FakeJudge(0.3)
    a = Assertion(type=AssertionType.GROUNDEDNESS, min_score=0.8)
    res = await assert_groundedness(_traj(final="unsupported claim"), a, judge=judge)
    assert res.passed is False
