"""Unit tests for ``prismal.agents.skynet.reduce`` (SPEC-SKY-RED-001).

Covers RF-SKY-06 and the S3 "done when" criteria: reduce excludes failures
from the synthesis but retains them for the caller; each strategy behaves per
spec.
"""

from __future__ import annotations

import pytest

from prismal.agents.skynet.reduce import reduce_results
from prismal.agents.skynet.types import WorkerResult
from prismal.core.config import Settings
from prismal.core.exceptions import SkynetConfigError

# ── fixtures / fakes ─────────────────────────────────────────────────────────


def _ok(order_id: str, output: str) -> WorkerResult:
    return WorkerResult(order_id=order_id, output=output, success=True)


def _fail(order_id: str, error: str = "boom") -> WorkerResult:
    return WorkerResult(order_id=order_id, output="", success=False, error=error)


class FakeReducer:
    """Deterministic reduce_fn capturing (goal, results)."""

    def __init__(self, reply: str = "synthesized") -> None:
        self.reply = reply
        self.calls: list[tuple[str, list[WorkerResult]]] = []

    async def __call__(self, goal: str, results: list[WorkerResult]) -> str:
        self.calls.append((goal, results))
        return self.reply


# ── strategy: concat ─────────────────────────────────────────────────────────


async def test_concat_joins_successful_outputs_deterministically() -> None:
    """concat joins successes in order, no LLM involved."""
    results = [_ok("ord-1", "alpha"), _ok("ord-2", "beta")]
    answer = await reduce_results("g", results, strategy="concat", settings=Settings())
    assert "alpha" in answer and "beta" in answer
    assert answer.index("alpha") < answer.index("beta")
    assert "ord-1" in answer and "ord-2" in answer


async def test_concat_excludes_failures() -> None:
    """Failed results never leak into the concatenated answer."""
    results = [_ok("ord-1", "alpha"), _fail("ord-2")]
    answer = await reduce_results("g", results, strategy="concat", settings=Settings())
    assert "alpha" in answer
    assert "ord-2" not in answer


async def test_concat_with_no_successes_is_empty() -> None:
    """All-failed rounds reduce to an empty answer."""
    answer = await reduce_results("g", [_fail("ord-1")], strategy="concat", settings=Settings())
    assert answer == ""


async def test_settings_default_resolves_via_get_settings() -> None:
    """Omitting settings falls back to get_settings() (lazy default)."""
    answer = await reduce_results("g", [_ok("ord-1", "alpha")], strategy="concat")
    assert "alpha" in answer


# ── strategy: first_success ──────────────────────────────────────────────────


async def test_first_success_returns_earliest_successful_output() -> None:
    """first_success picks the earliest success, skipping failures."""
    results = [_fail("ord-1"), _ok("ord-2", "winner"), _ok("ord-3", "later")]
    answer = await reduce_results("g", results, strategy="first_success", settings=Settings())
    assert answer == "winner"


async def test_first_success_with_no_successes_is_empty() -> None:
    """No successful worker → empty answer."""
    answer = await reduce_results(
        "g", [_fail("ord-1")], strategy="first_success", settings=Settings()
    )
    assert answer == ""


# ── strategy: synthesis (default) ────────────────────────────────────────────


async def test_synthesis_calls_reduce_fn_with_successes_only() -> None:
    """synthesis hands the injected reduce_fn only the successful results."""
    reducer = FakeReducer("combined")
    results = [_ok("ord-1", "alpha"), _fail("ord-2"), _ok("ord-3", "gamma")]
    answer = await reduce_results("the goal", results, reduce_fn=reducer, settings=Settings())
    assert answer == "combined"
    goal, passed = reducer.calls[0]
    assert goal == "the goal"
    assert [r.order_id for r in passed] == ["ord-1", "ord-3"]
    assert all(r.success for r in passed)


async def test_synthesis_is_the_default_strategy() -> None:
    """Omitting strategy uses synthesis (SPEC default)."""
    reducer = FakeReducer("via default")
    answer = await reduce_results("g", [_ok("o", "x")], reduce_fn=reducer, settings=Settings())
    assert answer == "via default"


async def test_synthesis_with_no_successes_short_circuits_to_empty() -> None:
    """Nothing to synthesize → empty answer; reduce_fn never called."""
    reducer = FakeReducer()
    answer = await reduce_results("g", [_fail("ord-1")], reduce_fn=reducer, settings=Settings())
    assert answer == ""
    assert reducer.calls == []


async def test_synthesis_failure_falls_back_to_concat() -> None:
    """A failing reduce backend degrades to the deterministic concat."""

    async def boom(goal: str, results: list[WorkerResult]) -> str:
        raise RuntimeError("llm down")

    results = [_ok("ord-1", "alpha"), _ok("ord-2", "beta")]
    answer = await reduce_results("g", results, reduce_fn=boom, settings=Settings())
    assert "alpha" in answer and "beta" in answer


# ── invalid strategy ─────────────────────────────────────────────────────────


async def test_unknown_strategy_raises_config_error() -> None:
    """An unknown strategy is a configuration error."""
    with pytest.raises(SkynetConfigError):
        await reduce_results(
            "g",
            [_ok("ord-1", "x")],
            strategy="majority",  # type: ignore[arg-type]
            settings=Settings(),
        )
