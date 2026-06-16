"""Agent evaluation harness — runnable example (Phase V).

Runs a tiny eval-set through an ``EvalRunner`` with *injected fakes* (a scripted
graph + a no-op runtime), so it executes offline with no API keys. Swap the
injected factories for the defaults (``EvalRunner()``) to run against the real
compiled graph with ``build_test_runtime`` fakes.

    uv run python examples/agent_eval.py
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from prismal.eval.regression import compare
from prismal.eval.report import to_json, to_markdown
from prismal.eval.runner import EvalRunner
from prismal.eval.types import Assertion, AssertionType, EvalCase, EvalSet


class _ScriptedGraph:
    """A graph double whose astream replays a fixed answer for any input."""

    def __init__(self, answer: str) -> None:
        self._answer = answer

    async def astream(
        self, _input: Any, _config: Any = None, *, stream_mode: str = "updates"
    ) -> AsyncIterator[dict[str, Any]]:
        yield {"supervisor": {"messages": [AIMessage(content=self._answer)]}}


class _NoopRuntime:
    tool_provider = None

    async def aclose(self) -> None:
        return None


def _runner(answer: str) -> EvalRunner:
    async def graph_factory(**_k: Any) -> Any:
        return _ScriptedGraph(answer)

    def runtime_factory(**_k: Any) -> Any:
        return _NoopRuntime()

    return EvalRunner(graph_factory=graph_factory, runtime_factory=runtime_factory)


async def main() -> None:
    eval_set = EvalSet(
        suite="example",
        cases=[
            EvalCase(
                id="greeting",
                input="Say hi.",
                assertions=[Assertion(type=AssertionType.EXACT, expected="hi there")],
            ),
            EvalCase(
                id="tool-bounds",
                input="Answer directly.",
                assertions=[Assertion(type=AssertionType.TOOL_USAGE, max_steps=3)],
            ),
        ],
    )

    card = await _runner("hi there").run_set(eval_set)
    print(to_markdown(card))
    print("--- JSON ---")
    print(to_json(card))

    # Regression gate: compare against a (here, identical) baseline.
    result = compare(card, card)
    print(f"\nregression gate passed: {result.passed}")


if __name__ == "__main__":
    asyncio.run(main())
