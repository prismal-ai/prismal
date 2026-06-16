"""Tests for the EvalRunner (Phase V — SPEC-EVL-RUN-001).

The runner is exercised with injected fakes: a ``graph_factory`` returning a
scripted fake graph and a ``runtime_factory`` returning a no-op runtime, so the
orchestration (build runtime → drive graph → capture → assemble result) is
tested without an LLM. ``run_case`` must never raise.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage

from prismal.eval.runner import EvalRunner
from prismal.eval.types import Assertion, AssertionType, EvalCase, EvalSet


class _FakeGraph:
    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self._chunks = chunks

    async def astream(
        self, _input: Any, _config: Any = None, *, stream_mode: str = "updates"
    ) -> AsyncIterator[dict[str, Any]]:
        for chunk in self._chunks:
            yield chunk


class _BoomGraph:
    async def astream(self, *_a: Any, **_k: Any) -> AsyncIterator[dict[str, Any]]:
        raise RuntimeError("graph blew up")
        yield {}  # pragma: no cover — unreachable, makes this an async generator


class _FakeRuntime:
    """A RuntimeContext double exposing the attributes the runner touches."""

    def __init__(self) -> None:
        self.tool_provider = object()
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


def _runner(graph: Any, *, runtime: Any | None = None) -> tuple[EvalRunner, _FakeRuntime]:
    rt = runtime or _FakeRuntime()

    async def graph_factory(**_kwargs: Any) -> Any:
        return graph

    def runtime_factory(**_kwargs: Any) -> Any:
        return rt

    return EvalRunner(graph_factory=graph_factory, runtime_factory=runtime_factory), rt


def _answer_case(assertions: list[Assertion] | None = None) -> EvalCase:
    return EvalCase(id="c1", input="hi", assertions=assertions or [])


async def test_run_case_returns_caseresult_with_trajectory() -> None:
    """A case with no assertions passes and carries the captured trajectory."""
    graph = _FakeGraph([{"supervisor": {"messages": [AIMessage(content="hello")]}}])
    runner, rt = _runner(graph)

    result = await runner.run_case(_answer_case())

    assert result.case_id == "c1"
    assert result.passed is True
    assert result.assertion_results == []
    assert result.trajectory.final_answer == "hello"
    assert result.trajectory.visited_nodes == ["supervisor"]
    assert rt.closed is True  # runtime is always torn down


async def test_run_case_never_raises_on_graph_error() -> None:
    """A graph that raises mid-stream yields a failed, non-terminated result."""
    runner, rt = _runner(_BoomGraph())

    result = await runner.run_case(_answer_case())

    assert result.passed is False
    assert result.trajectory.terminated is False
    assert rt.closed is True


async def test_run_case_is_deterministic_in_structure() -> None:
    """The same case run twice yields identical structural results."""
    chunks = [{"supervisor": {"messages": [AIMessage(content="answer")]}}]
    runner, _ = _runner(_FakeGraph(chunks))

    r1 = await runner.run_case(_answer_case())
    runner2, _ = _runner(_FakeGraph(chunks))
    r2 = await runner2.run_case(_answer_case())

    assert r1.trajectory.final_answer == r2.trajectory.final_answer
    assert r1.trajectory.visited_nodes == r2.trajectory.visited_nodes
    assert r1.passed == r2.passed


async def test_run_set_aggregates_scorecard() -> None:
    """run_set runs every case and aggregates suite-level metrics."""
    chunks = [{"supervisor": {"messages": [AIMessage(content="ok")]}}]
    runner, _ = _runner(_FakeGraph(chunks))
    eval_set = EvalSet(
        suite="smoke",
        cases=[_answer_case(), EvalCase(id="c2", input="yo", assertions=[])],
    )

    card = await runner.run_set(eval_set)

    assert card.suite == "smoke"
    assert len(card.cases) == 2
    assert card.pass_rate == 1.0
    assert card.tool_error_rate == 0.0
    assert card.avg_steps >= 0.0


def test_assertion_type_for_future_dispatch_is_typed() -> None:
    """Sanity: an EvalCase can carry assertions the runner will later dispatch."""
    case = _answer_case([Assertion(type=AssertionType.EXACT, expected="ok")])
    assert case.assertions[0].type is AssertionType.EXACT


async def test_scorecard_stamps_prismal_version() -> None:
    """The scorecard version is the prismal package version, not a dependency's."""
    from importlib.metadata import version

    chunks = [{"supervisor": {"messages": [AIMessage(content="ok")]}}]
    runner, _ = _runner(_FakeGraph(chunks))
    card = await runner.run_set(EvalSet(suite="s", cases=[_answer_case()]))
    assert card.version == version("prismal-ai")
