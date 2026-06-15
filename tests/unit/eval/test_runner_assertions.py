"""Runner wires real assertion dispatch (Phase V — V3 integration).

With V3, ``EvalRunner.run_case`` evaluates each assertion against the captured
trajectory (exact/tool_usage/semantic/llm_judge/groundedness) and folds the
results into ``passed``. Exercised with injected fakes — no LLM, no real graph.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage

from prismal.eval.judges import Judge
from prismal.eval.runner import EvalRunner
from prismal.eval.types import Assertion, AssertionType, EvalCase


class _FakeGraph:
    def __init__(self, answer: str) -> None:
        self._answer = answer

    async def astream(
        self, _input: Any, _config: Any = None, *, stream_mode: str = "updates"
    ) -> AsyncIterator[dict[str, Any]]:
        yield {"supervisor": {"messages": [AIMessage(content=self._answer)]}}


class _FakeEmbeddings:
    async def aembed_query(self, text: str) -> list[float]:
        return {"hello": [1.0, 0.0], "hi": [1.0, 0.0]}.get(text, [0.0, 1.0])


class _FakeRuntime:
    def __init__(self) -> None:
        self.tool_provider = object()
        self.embeddings = _FakeEmbeddings()

    async def aclose(self) -> None:
        return None


def _runner(answer: str, *, judge: Judge | None = None) -> EvalRunner:
    rt = _FakeRuntime()

    async def graph_factory(**_k: Any) -> Any:
        return _FakeGraph(answer)

    def runtime_factory(**_k: Any) -> Any:
        return rt

    return EvalRunner(graph_factory=graph_factory, runtime_factory=runtime_factory, judge=judge)


async def test_run_case_passes_when_exact_assertion_matches() -> None:
    case = EvalCase(
        id="c1", input="x", assertions=[Assertion(type=AssertionType.EXACT, expected="42")]
    )
    result = await _runner("42").run_case(case)
    assert result.passed is True
    assert len(result.assertion_results) == 1
    assert result.assertion_results[0].passed is True


async def test_run_case_fails_when_exact_assertion_mismatches() -> None:
    case = EvalCase(
        id="c1", input="x", assertions=[Assertion(type=AssertionType.EXACT, expected="42")]
    )
    result = await _runner("43").run_case(case)
    assert result.passed is False
    assert result.assertion_results[0].passed is False


async def test_run_case_evaluates_semantic_with_runtime_embeddings() -> None:
    case = EvalCase(
        id="c1",
        input="x",
        assertions=[Assertion(type=AssertionType.SEMANTIC, expected="hi", min_score=0.9)],
    )
    result = await _runner("hello").run_case(case)
    assert result.passed is True  # cosine([1,0],[1,0]) == 1.0


async def test_run_case_uses_injected_judge_for_llm_assertion() -> None:
    judge = Judge(judge_fn=lambda _p: _async(0.95))
    case = EvalCase(
        id="c1",
        input="x",
        assertions=[Assertion(type=AssertionType.LLM_JUDGE, rubric="ok", min_score=0.7)],
    )
    result = await _runner("anything", judge=judge).run_case(case)
    assert result.passed is True


async def test_run_case_fails_on_one_failing_assertion_of_many() -> None:
    case = EvalCase(
        id="c1",
        input="x",
        assertions=[
            Assertion(type=AssertionType.EXACT, expected="42"),
            Assertion(type=AssertionType.TOOL_USAGE, never_call=["supervisor"]),
        ],
    )
    # final answer matches exact, but the supervisor node was visited (forbidden).
    result = await _runner("42").run_case(case)
    assert result.passed is False


def _async(value: float):
    async def _coro() -> float:
        return value

    return _coro()
