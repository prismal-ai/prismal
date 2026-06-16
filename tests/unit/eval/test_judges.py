"""Tests for the LLM-as-judge (Phase V — SPEC-EVL-JDG-001).

The judge is exercised with an injected ``judge_fn`` so no LLM is called. It must
route the (untrusted) answer/context through ``SecurePromptBuilder`` and clamp
its score to ``[0, 1]``. The default judge_fn (live) is out of scope here; its
pure score-parsing helper is tested directly.
"""

from __future__ import annotations

from prismal.eval.judges import Judge, _parse_score


async def test_judge_returns_clamped_score() -> None:
    """The judge returns exactly what judge_fn yields when already in range."""
    judge = Judge(judge_fn=lambda _prompt: _async(0.73))
    score = await judge.score(rubric="cites the source", answer="it cites X")
    assert score == 0.73


async def test_judge_clamps_out_of_range_scores() -> None:
    """Scores outside [0, 1] are clamped, never propagated."""
    high = Judge(judge_fn=lambda _p: _async(1.8))
    low = Judge(judge_fn=lambda _p: _async(-0.5))
    assert await high.score(rubric="r", answer="a") == 1.0
    assert await low.score(rubric="r", answer="a") == 0.0


async def test_judge_isolates_answer_and_includes_rubric() -> None:
    """The answer is wrapped by SecurePromptBuilder; the rubric reaches the prompt."""
    seen: dict[str, str] = {}

    def capture(prompt: str) -> object:
        seen["prompt"] = prompt
        return _async(0.5)

    judge = Judge(judge_fn=capture)
    await judge.score(rubric="UNIQUE_RUBRIC_TOKEN", answer="hello answer")

    assert "UNIQUE_RUBRIC_TOKEN" in seen["prompt"]
    # SecurePromptBuilder isolates untrusted input inside <user_input> tags.
    assert "<user_input>hello answer</user_input>" in seen["prompt"]


async def test_judge_includes_context_as_document() -> None:
    """Groundedness context is passed through as an isolated document block."""
    seen: dict[str, str] = {}

    def capture(prompt: str) -> object:
        seen["prompt"] = prompt
        return _async(0.5)

    judge = Judge(judge_fn=capture)
    await judge.score(rubric="r", answer="a", context="RETRIEVED_CTX")

    assert "RETRIEVED_CTX" in seen["prompt"]


# ── _parse_score helper ───────────────────────────────────────────────────────


def test_parse_score_reads_decimal() -> None:
    assert _parse_score("Score: 0.85 — good") == 0.85


def test_parse_score_reads_bare_float() -> None:
    assert _parse_score("0.42") == 0.42


def test_parse_score_clamps_and_defaults() -> None:
    assert _parse_score("no number here") == 0.0
    assert _parse_score("1") == 1.0


def _async(value: float):
    """Wrap a value in an already-resolved awaitable."""

    async def _coro() -> float:
        return value

    return _coro()
