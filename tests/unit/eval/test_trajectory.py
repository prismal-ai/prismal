"""Tests for trajectory capture from a graph stream (Phase V — SPEC-EVL-TRJ-001).

These drive ``capture_trajectory`` with a *fake* graph whose ``astream`` yields a
scripted ``updates`` event sequence, so the capture logic is tested without an
LLM or the real compiled graph. Determinism: no network, no clock dependency
(an injected clock makes latency exact).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from prismal.eval.trajectory import capture_trajectory
from prismal.eval.types import Assertion, AssertionType, EvalCase


class _FakeGraph:
    """A graph double whose ``astream`` replays a fixed list of update chunks."""

    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self._chunks = chunks

    async def astream(
        self, _input: Any, _config: Any = None, *, stream_mode: str = "updates"
    ) -> AsyncIterator[dict[str, Any]]:
        assert stream_mode == "updates"
        for chunk in self._chunks:
            yield chunk


def _case() -> EvalCase:
    return EvalCase(
        id="t1",
        input="What does the budget hard cap do?",
        assertions=[Assertion(type=AssertionType.EXACT, expected="x")],
    )


def _clock() -> Any:
    """A deterministic monotonic clock advancing 5ms per call."""
    ticks = iter([0.0, 0.005])  # seconds: start, end
    return lambda: next(ticks)


async def test_capture_collects_visited_nodes_and_final_answer() -> None:
    """Visited nodes are recorded in order and the last AI answer is the final."""
    chunks = [
        {"supervisor": {"messages": [AIMessage(content="routing")]}},
        {"rag_agent": {"messages": [AIMessage(content="The hard cap aborts the run.")]}},
    ]
    traj = await capture_trajectory(_FakeGraph(chunks), _case(), clock=_clock())

    assert traj.case_id == "t1"
    assert traj.visited_nodes == ["supervisor", "rag_agent"]
    assert traj.final_answer == "The hard cap aborts the run."
    assert traj.terminated is True
    assert traj.latency_ms == 5.0


async def test_capture_counts_tool_calls_and_errors() -> None:
    """Tool calls from AI messages are counted; failed ToolMessages are errors."""
    chunks = [
        {
            "coder": {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {"name": "write_file", "args": {"path": "a.py"}, "id": "1"},
                        ],
                    ),
                ]
            }
        },
        {
            "coder": {
                "messages": [
                    ToolMessage(content="ok", name="write_file", tool_call_id="1"),
                    ToolMessage(content="boom", name="run_tests", tool_call_id="2", status="error"),
                ]
            }
        },
        {"coder": {"messages": [AIMessage(content="done")]}},
    ]
    traj = await capture_trajectory(_FakeGraph(chunks), _case(), clock=_clock())

    assert traj.tool_calls == 1
    assert traj.tool_errors == 1
    assert traj.final_answer == "done"
    # A step exists for the tool call carrying its name + args.
    tool_steps = [s for s in traj.steps if s.tool_name == "write_file"]
    assert tool_steps and tool_steps[0].tool_args == {"path": "a.py"}


async def test_capture_extracts_token_usage() -> None:
    """Tokens accumulate from AI-message usage_metadata across the stream."""
    msg = AIMessage(content="answer")
    msg.usage_metadata = {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}
    chunks = [{"supervisor": {"messages": [msg]}}]
    traj = await capture_trajectory(_FakeGraph(chunks), _case(), clock=_clock())

    assert traj.tokens == 14


async def test_capture_empty_stream_is_not_terminated_answerless() -> None:
    """An empty stream yields an empty, terminated trajectory (no crash)."""
    traj = await capture_trajectory(_FakeGraph([]), _case(), clock=_clock())

    assert traj.visited_nodes == []
    assert traj.final_answer == ""
    assert traj.tool_calls == 0
    assert traj.terminated is True
