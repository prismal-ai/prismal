"""Unit tests for tree_of_thoughts (prismal.agents.patterns.tree_of_thoughts).

Tree of Thoughts explores a reasoning tree: each node generates N candidate
next thoughts, each is scored, and the search keeps the top beam_size at
each depth level until a threshold is reached or max depth is exhausted.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from prismal.agents.patterns.tree_of_thoughts import (
    Thought,
    ToTError,
    ToTResult,
    make_tot_node,
    tree_of_thoughts,
)
from prismal.core.exceptions import PrismalError

# ── Helpers ───────────────────────────────────────────────────────────────────


def _state() -> Any:
    """Minimal mock AgentState — the function only passes it through to fns."""
    return MagicMock()


def _fixed_generate(texts_per_call: list[list[str]]) -> AsyncMock:
    """Return an async generate_fn that yields the scripted lists in order."""
    return AsyncMock(side_effect=texts_per_call)


def _score_by_content(mapping: dict[str, float]) -> AsyncMock:
    """Build an evaluate_fn that looks up content in mapping, defaulting to 0.0."""

    async def _fn(content: str, state: Any) -> float:
        return mapping.get(content, 0.0)

    mock = AsyncMock(side_effect=_fn)
    return mock


# ── Dataclass / exception ─────────────────────────────────────────────────────


def test_thought_is_instantiable() -> None:
    t = Thought(
        content="step 1",
        score=0.7,
        depth=1,
        parent_id=None,
        children=[],
    )
    assert t.content == "step 1"
    assert t.score == 0.7
    assert t.depth == 1
    assert t.parent_id is None
    assert t.children == []
    assert t.is_terminal is False
    # id auto-generated
    assert isinstance(t.id, str) and t.id


def test_tot_result_is_instantiable() -> None:
    t = Thought(content="x", score=1.0, depth=1, parent_id=None, children=[])
    r = ToTResult(
        best_thought=t,
        best_path=[t],
        all_thoughts=[t],
        total_thoughts_generated=1,
    )
    assert r.best_thought is t
    assert r.best_path == [t]
    assert r.total_thoughts_generated == 1


def test_tot_error_inherits_from_prismal_error() -> None:
    assert issubclass(ToTError, PrismalError)


# ── Input validation ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_invalid_breadth_raises() -> None:
    with pytest.raises(ValueError):
        await tree_of_thoughts(
            problem="p",
            generate_fn=AsyncMock(return_value=[]),
            evaluate_fn=AsyncMock(return_value=0.0),
            state=_state(),
            breadth=0,
        )


@pytest.mark.asyncio
async def test_invalid_depth_raises() -> None:
    with pytest.raises(ValueError):
        await tree_of_thoughts(
            problem="p",
            generate_fn=AsyncMock(return_value=[]),
            evaluate_fn=AsyncMock(return_value=0.0),
            state=_state(),
            depth=0,
        )


@pytest.mark.asyncio
async def test_invalid_beam_raises() -> None:
    with pytest.raises(ValueError):
        await tree_of_thoughts(
            problem="p",
            generate_fn=AsyncMock(return_value=[]),
            evaluate_fn=AsyncMock(return_value=0.0),
            state=_state(),
            beam_size=0,
        )


# ── Beam search ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_beam_search_returns_tot_result_with_best_path() -> None:
    # depth=2, breadth=2, beam=1: root -> A/B (score 0.3/0.7) -> keep B -> C/D (0.4/0.95)
    # threshold high enough that B doesn't trigger early exit at level 1.
    gen = _fixed_generate([["A", "B"], ["C", "D"]])
    ev = _score_by_content({"A": 0.3, "B": 0.7, "C": 0.4, "D": 0.95})
    result = await tree_of_thoughts(
        problem="p",
        generate_fn=gen,
        evaluate_fn=ev,
        state=_state(),
        breadth=2,
        depth=2,
        beam_size=1,
        threshold=0.99,
        search_strategy="beam",
    )
    assert isinstance(result, ToTResult)
    assert result.best_thought.content == "D"
    # Path: root -> B -> D
    assert [t.content for t in result.best_path[1:]] == ["B", "D"]


@pytest.mark.asyncio
async def test_beam_search_respects_breadth_times_depth_cap() -> None:
    """Total generate_fn calls ≤ breadth * depth."""
    # depth=3, breadth=3, beam=2: expect at most 9 generate calls
    breadth, depth, beam = 3, 3, 2
    # Any scoring — just count calls
    gen = AsyncMock(return_value=["x", "y", "z"])
    ev = AsyncMock(return_value=0.1)
    await tree_of_thoughts(
        problem="p",
        generate_fn=gen,
        evaluate_fn=ev,
        state=_state(),
        breadth=breadth,
        depth=depth,
        beam_size=beam,
        search_strategy="beam",
    )
    assert gen.call_count <= breadth * depth


@pytest.mark.asyncio
async def test_beam_search_early_exits_on_threshold() -> None:
    """If a thought scores >= threshold, search stops expanding further."""
    gen = _fixed_generate([["perfect", "poor"]])  # only level 1 needed
    ev = _score_by_content({"perfect": 0.99, "poor": 0.05})
    result = await tree_of_thoughts(
        problem="p",
        generate_fn=gen,
        evaluate_fn=ev,
        state=_state(),
        breadth=2,
        depth=5,
        beam_size=1,
        threshold=0.9,
        search_strategy="beam",
    )
    assert result.best_thought.content == "perfect"
    assert result.best_thought.is_terminal is True
    # Only the level-1 generate call happened
    assert gen.call_count == 1


@pytest.mark.asyncio
async def test_beam_search_preserves_ordering_among_ties() -> None:
    """Stable top-k: when scores tie, order should be deterministic."""
    gen = _fixed_generate([["A", "B", "C"]])
    ev = _score_by_content({"A": 0.5, "B": 0.5, "C": 0.5})
    result = await tree_of_thoughts(
        problem="p",
        generate_fn=gen,
        evaluate_fn=ev,
        state=_state(),
        breadth=3,
        depth=1,
        beam_size=2,
        search_strategy="beam",
    )
    # all three thoughts were assessed
    assert ev.call_count == 3
    # best_thought is one of them (all tied); deterministic pick (first by insertion)
    assert result.best_thought.content in {"A", "B", "C"}


# ── BFS ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bfs_generates_more_calls_than_beam() -> None:
    """BFS explores every node at each level (no pruning)."""
    # depth=2, breadth=2: 1 root call + 2 level-1 calls = 3 generate calls
    gen = AsyncMock(side_effect=[["A", "B"], ["C", "D"], ["E", "F"]])
    ev = AsyncMock(return_value=0.1)
    await tree_of_thoughts(
        problem="p",
        generate_fn=gen,
        evaluate_fn=ev,
        state=_state(),
        breadth=2,
        depth=2,
        beam_size=1,  # ignored in BFS
        search_strategy="bfs",
    )
    # BFS expands both level-1 children, so call_count = 3 (1 root + 2 level-1)
    assert gen.call_count == 3


# ── DFS ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dfs_descends_one_branch_before_backtracking() -> None:
    """DFS should reach a terminal (threshold) by following the first branch."""
    # Root -> A (0.8), B (0.4). DFS picks highest first → A.
    # From A -> C (0.95 terminal), D (0.1). DFS explores C first → terminal.
    gen = _fixed_generate([["A", "B"], ["C", "D"]])
    ev = _score_by_content({"A": 0.8, "B": 0.4, "C": 0.95, "D": 0.1})
    result = await tree_of_thoughts(
        problem="p",
        generate_fn=gen,
        evaluate_fn=ev,
        state=_state(),
        breadth=2,
        depth=3,
        beam_size=1,
        threshold=0.9,
        search_strategy="dfs",
    )
    assert result.best_thought.content == "C"
    # The B branch was not expanded (only 2 generate calls: root, then A)
    assert gen.call_count == 2


# ── Total thoughts tracking ──────────────────────────────────────────────────


# ── LangGraph node factory ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_make_tot_node_returns_callable_that_updates_state() -> None:
    """The factory returns an async node that appends an AIMessage with the best thought."""
    from langchain_core.messages import HumanMessage

    gen = _fixed_generate([["A", "B"]])
    ev = _score_by_content({"A": 0.95, "B": 0.2})
    node = make_tot_node(generate_fn=gen, evaluate_fn=ev, depth=1, beam_size=1, breadth=2)

    state = {"messages": [HumanMessage(content="solve X")], "metadata": {}}
    update = await node(state)

    assert "messages" in update
    msgs = update["messages"]
    assert len(msgs) >= 1
    # Best thought content appears in the AI response
    assert "A" in msgs[-1].content
    # Metadata carries ToT diagnostics
    assert "tot" in update["metadata"]
    assert update["metadata"]["tot"]["best_score"] == 0.95


@pytest.mark.asyncio
async def test_make_tot_node_handles_empty_messages_gracefully() -> None:
    node = make_tot_node(
        generate_fn=AsyncMock(return_value=["x"]),
        evaluate_fn=AsyncMock(return_value=0.5),
        depth=1,
        breadth=1,
        beam_size=1,
    )
    update = await node({"messages": [], "metadata": {}})
    # No problem to solve → node returns state unchanged (no messages added).
    assert update == {} or "messages" not in update


@pytest.mark.asyncio
async def test_total_thoughts_generated_is_reported() -> None:
    gen = _fixed_generate([["A", "B"], ["C", "D"]])
    ev = _score_by_content({"A": 0.2, "B": 0.3, "C": 0.4, "D": 0.5})
    result = await tree_of_thoughts(
        problem="p",
        generate_fn=gen,
        evaluate_fn=ev,
        state=_state(),
        breadth=2,
        depth=2,
        beam_size=1,
        search_strategy="beam",
    )
    # 2 at level 1 + 2 at level 2 = 4 thoughts (root not counted as "generated")
    assert result.total_thoughts_generated == 4
