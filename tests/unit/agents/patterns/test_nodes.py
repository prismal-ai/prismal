"""Tests for the advanced-pattern LangGraph node factories (Phase D / D1-01).

Each ``make_*_node`` factory turns one reasoning pattern into a LangGraph
node: ``async (state) -> state_update``. The node extracts the user query
from ``state["messages"][-1]``, runs the pattern, and returns an
``AIMessage`` plus a ``metadata[<pattern>]`` diagnostics block.

The factories accept injected callables/clients so these tests run without
any LLM backend. A small ``_FakeLLM`` exercises the LLM-backed defaults for
the patterns that need generator/evaluator/planner functions.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import HumanMessage

from prismal.agents.patterns.nodes import (
    _default_compiler_plan,
    _default_tot_evaluate,
    _default_tot_generate,
    make_constitutional_node,
    make_debate_node,
    make_lats_node,
    make_llm_compiler_node,
    make_mixture_node,
    make_tot_agent_node,
)

# --------------------------------------------------------------------------- #
# Test doubles                                                                 #
# --------------------------------------------------------------------------- #


class _FakeLLM:
    """Minimal async LLM stub: returns a fixed ``.content`` for any prompt."""

    def __init__(self, response: str) -> None:
        self._response = response

    async def ainvoke(self, _messages: Any) -> SimpleNamespace:
        return SimpleNamespace(content=self._response)


def _state(text: str) -> dict[str, Any]:
    return {"messages": [HumanMessage(content=text)], "metadata": {}}


# --------------------------------------------------------------------------- #
# Debate node                                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_debate_node_appends_consensus_and_metadata() -> None:
    async def fake_debate(**kwargs: Any) -> SimpleNamespace:
        assert kwargs["query"] == "is X better than Y?"
        return SimpleNamespace(
            consensus="On balance, X wins.",
            agreement_score=0.75,
            rounds_completed=2,
        )

    node = make_debate_node(debate_fn=fake_debate)
    update = await node(_state("is X better than Y?"))

    assert update["messages"][-1].content == "On balance, X wins."
    assert update["metadata"]["debate"]["agreement_score"] == 0.75
    assert update["metadata"]["debate"]["rounds_completed"] == 2


@pytest.mark.asyncio
async def test_debate_node_empty_messages_is_noop() -> None:
    node = make_debate_node(debate_fn=_unused_async)
    update = await node({"messages": [], "metadata": {}})
    assert update == {}


# --------------------------------------------------------------------------- #
# Constitutional node                                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_constitutional_node_filters_last_message() -> None:
    class _FakeFilter:
        async def apply(self, output: str, context: str | None = None) -> SimpleNamespace:
            assert output == "raw answer"
            return SimpleNamespace(
                final_output="clean answer",
                revisions=[object()],
                all_principles_satisfied=True,
                max_revisions_reached=False,
            )

    node = make_constitutional_node(filter_factory=_FakeFilter)
    update = await node(_state("raw answer"))

    assert update["messages"][-1].content == "clean answer"
    assert update["metadata"]["constitutional"]["revisions"] == 1
    assert update["metadata"]["constitutional"]["all_principles_satisfied"] is True


@pytest.mark.asyncio
async def test_constitutional_node_empty_messages_is_noop() -> None:
    node = make_constitutional_node(filter_factory=object)
    update = await node({"messages": [], "metadata": {}})
    assert update == {}


# --------------------------------------------------------------------------- #
# Mixture-of-agents node                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_mixture_node_uses_injected_moa() -> None:
    class _FakeMoA:
        async def generate(self, query: str, state: Any) -> SimpleNamespace:
            assert query == "summarise the report"
            return SimpleNamespace(
                final_answer="A concise summary.",
                providers_used=["claude-sonnet-4-5", "gpt-4o-mini"],
            )

    node = make_mixture_node(moa=_FakeMoA())
    update = await node(_state("summarise the report"))

    assert update["messages"][-1].content == "A concise summary."
    assert update["metadata"]["mixture"]["providers_used"] == [
        "claude-sonnet-4-5",
        "gpt-4o-mini",
    ]


@pytest.mark.asyncio
async def test_mixture_node_empty_messages_is_noop() -> None:
    node = make_mixture_node(moa=SimpleNamespace())
    update = await node({"messages": [], "metadata": {}})
    assert update == {}


# --------------------------------------------------------------------------- #
# Tree-of-Thoughts node                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_tot_node_with_injected_callables() -> None:
    async def gen(_problem: str, _state: Any, _path: list[Any]) -> list[str]:
        return ["candidate A", "candidate B"]

    async def ev(content: str, _state: Any) -> float:
        return 0.95 if "A" in content else 0.1

    node = make_tot_agent_node(generate_fn=gen, score_fn=ev, depth=1, breadth=2, beam_size=1)
    update = await node(_state("solve the puzzle"))

    assert "A" in update["messages"][-1].content
    assert update["metadata"]["tot"]["best_score"] == 0.95


@pytest.mark.asyncio
async def test_tot_node_empty_messages_is_noop() -> None:
    node = make_tot_agent_node(
        generate_fn=_unused_async, score_fn=_unused_async, depth=1, breadth=1, beam_size=1
    )
    update = await node({"messages": [], "metadata": {}})
    assert update == {} or "messages" not in update


# --------------------------------------------------------------------------- #
# Default ToT callables (LLM-backed parsing)                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_default_tot_generate_parses_lines() -> None:
    llm = _FakeLLM("First idea\nSecond idea\n\n  Third idea  ")
    gen = _default_tot_generate(llm, breadth=3)
    thoughts = await gen("problem", {}, [])
    assert thoughts == ["First idea", "Second idea", "Third idea"]


@pytest.mark.asyncio
async def test_default_tot_evaluate_parses_and_clamps() -> None:
    assert await _default_tot_evaluate(_FakeLLM("0.8"))("c", {}) == 0.8
    # Out-of-range is clamped to [0, 1]
    assert await _default_tot_evaluate(_FakeLLM("5"))("c", {}) == 1.0
    # Non-numeric is pessimistic (0.0) rather than raising
    assert await _default_tot_evaluate(_FakeLLM("not a number"))("c", {}) == 0.0


# --------------------------------------------------------------------------- #
# LATS node                                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_lats_node_with_injected_callables() -> None:
    async def actions(_state: Any, _tools: Any) -> list[str]:
        return ["reach the goal"]

    async def transition(state: Any, action: Any) -> str:
        return f"{state}{action}".strip()

    async def reward(state: Any) -> float:
        return 0.99 if "reach the goal" in str(state) else 0.0

    node = make_lats_node(
        reward_fn=reward,
        action_generator=actions,
        transition_fn=transition,
        max_simulations=8,
        max_depth=2,
    )
    update = await node(_state("get to the goal"))

    assert "reach the goal" in update["messages"][-1].content
    assert update["metadata"]["lats"]["best_reward"] >= 0.99


@pytest.mark.asyncio
async def test_lats_node_empty_messages_is_noop() -> None:
    node = make_lats_node(
        reward_fn=_unused_async,
        action_generator=_unused_async,
        transition_fn=_unused_async,
    )
    update = await node({"messages": [], "metadata": {}})
    assert update == {}


# --------------------------------------------------------------------------- #
# LLM-Compiler node                                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_llm_compiler_node_with_injected_callables() -> None:
    from prismal.agents.patterns.llm_compiler import TaskNode

    async def plan(_goal: str, _state: Any, _prev: Any) -> list[TaskNode]:
        return [TaskNode(id="T1", description="answer it", tool="answer", args={})]

    async def executor(task: TaskNode, _prior: dict[str, Any]) -> str:
        return f"output for {task.id}"

    async def joiner(_goal: str, tasks: list[TaskNode]) -> str:
        return "final synthesised answer"

    node = make_llm_compiler_node(plan_fn=plan, tool_executor=executor, joiner=joiner)
    update = await node(_state("do a multi-step task"))

    assert update["messages"][-1].content == "final synthesised answer"
    assert update["metadata"]["llm_compiler"]["tasks_succeeded"] == 1
    assert update["metadata"]["llm_compiler"]["tasks_failed"] == 0


@pytest.mark.asyncio
async def test_default_compiler_plan_builds_tasknodes() -> None:
    llm = _FakeLLM("Research the topic\nDraft the summary")
    plan_fn = _default_compiler_plan(llm, max_subtasks=3)
    tasks = await plan_fn("write a brief", {}, None)
    assert [t.description for t in tasks] == ["Research the topic", "Draft the summary"]
    # Default tasks are independent (parallelisable) — no cross-dependencies.
    assert all(t.depends_on == [] for t in tasks)


@pytest.mark.asyncio
async def test_llm_compiler_node_empty_messages_is_noop() -> None:
    node = make_llm_compiler_node(
        plan_fn=_unused_async, tool_executor=_unused_async, joiner=_unused_async
    )
    update = await node({"messages": [], "metadata": {}})
    assert update == {}


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


async def _unused_async(*_args: Any, **_kwargs: Any) -> Any:  # pragma: no cover
    raise AssertionError("callable should not be invoked for empty-message state")
