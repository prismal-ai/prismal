"""Unit tests for LATSAgent (lightagent.agents.patterns.lats).

Language Agent Tree Search uses Monte Carlo Tree Search (MCTS) to explore
the space of agent actions. Tests cover:
- UCB1 mathematical correctness (standard formula).
- Basic MCTS flow (select → expand → simulate → backpropagate).
- Budget caps (max_simulations, timeout).
- Best action sequence extraction.

Action generation, transitions, and reward are mocked via injected callables
so no LLM or tool infrastructure is required.
"""

from __future__ import annotations

import asyncio
import math
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from lightagent.agents.patterns.lats import (
    LATSAgent,
    LATSError,
    LATSNode,
    LATSResult,
)
from lightagent.core.exceptions import LightAgentError

# ── Helpers ───────────────────────────────────────────────────────────────────


def _node(
    state: Any = None,
    action: Any = None,
    reward: float = 0.0,
    visits: int = 0,
    parent: LATSNode | None = None,
) -> LATSNode:
    return LATSNode(
        state=state,
        action=action,
        reward=reward,
        visits=visits,
        parent=parent,
    )


# ── Dataclass / exception ─────────────────────────────────────────────────────


def test_lats_node_is_instantiable_with_defaults() -> None:
    n = LATSNode(state="s", action=None)
    assert n.state == "s"
    assert n.action is None
    assert n.reward == 0.0
    assert n.visits == 0
    assert n.children == []
    assert n.is_terminal is False
    assert n.parent is None


def test_lats_result_is_instantiable() -> None:
    r = LATSResult(
        best_action_sequence=["a", "b"],
        final_state={"x": 1},
        total_simulations=10,
        best_reward=0.9,
        search_tree_depth=3,
    )
    assert r.best_action_sequence == ["a", "b"]
    assert r.total_simulations == 10


def test_lats_error_inherits_from_lightagent_error() -> None:
    assert issubclass(LATSError, LightAgentError)


# ── UCB1 math ────────────────────────────────────────────────────────────────


def test_ucb1_infinite_for_unvisited_child() -> None:
    """Unvisited nodes must always be selected first → UCB1 = +inf."""
    parent = _node(visits=10)
    child = _node(visits=0, parent=parent)
    assert child.ucb1() == math.inf


def test_ucb1_for_root_is_pure_exploitation() -> None:
    """Root (no parent) has no exploration term; UCB1 = Q/N."""
    root = _node(visits=5, reward=2.5)
    assert root.ucb1() == 0.5


def test_ucb1_matches_textbook_formula() -> None:
    """UCB1 = Q/N + C * sqrt(ln(N_parent) / N)."""
    parent = _node(visits=10)
    child = _node(visits=4, reward=3.0, parent=parent)
    c = 1.41
    expected = (3.0 / 4) + c * math.sqrt(math.log(10) / 4)
    assert child.ucb1(exploration_constant=c) == pytest.approx(expected, rel=1e-6)


def test_ucb1_higher_visits_lowers_exploration_bonus() -> None:
    """Exploration term shrinks with more visits at same parent."""
    parent = _node(visits=100)
    low_visits = _node(visits=1, reward=0.5, parent=parent)
    high_visits = _node(visits=50, reward=25.0, parent=parent)
    # Same average reward (0.5), but low_visits has higher exploration bonus.
    assert low_visits.ucb1() > high_visits.ucb1()


# ── Constructor ──────────────────────────────────────────────────────────────


def test_init_stores_config() -> None:
    agent = LATSAgent(
        tools=[],
        reward_fn=AsyncMock(return_value=0.5),
        action_generator=AsyncMock(return_value=[]),
        transition_fn=AsyncMock(),
        max_simulations=20,
        exploration_constant=2.0,
        max_depth=5,
        settings=MagicMock(),
    )
    assert agent._max_simulations == 20  # noqa: SLF001
    assert agent._exploration_constant == 2.0  # noqa: SLF001
    assert agent._max_depth == 5  # noqa: SLF001


def test_init_rejects_invalid_bounds() -> None:
    common = {
        "tools": [],
        "reward_fn": AsyncMock(return_value=0.0),
        "action_generator": AsyncMock(return_value=[]),
        "transition_fn": AsyncMock(),
        "settings": MagicMock(),
    }
    with pytest.raises(ValueError):
        LATSAgent(**common, max_simulations=0)
    with pytest.raises(ValueError):
        LATSAgent(**common, max_depth=0)
    with pytest.raises(ValueError):
        LATSAgent(**common, exploration_constant=-1.0)


# ── search() basic flow ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_returns_lats_result() -> None:
    # Generator produces 2 actions; transition just records which action was
    # taken; reward gives higher score to action "A".
    async def gen(state: Any, tools: Any) -> list[str]:
        depth = state.get("depth", 0)
        if depth >= 2:
            return []
        return ["A", "B"]

    async def transition(state: Any, action: str) -> dict[str, Any]:
        return {"depth": state.get("depth", 0) + 1, "path": state.get("path", []) + [action]}

    async def reward(state: Any) -> float:
        return 0.9 if "A" in state.get("path", []) else 0.1

    agent = LATSAgent(
        tools=[],
        reward_fn=reward,
        action_generator=gen,
        transition_fn=transition,
        max_simulations=20,
        max_depth=2,
        settings=MagicMock(),
    )
    result = await agent.search(initial_state={"depth": 0, "path": []}, goal="maximize")

    assert isinstance(result, LATSResult)
    assert result.total_simulations > 0
    assert result.best_reward >= 0.0
    # Best action sequence should prefer "A" (higher reward)
    assert "A" in result.best_action_sequence


@pytest.mark.asyncio
async def test_search_respects_max_simulations_cap() -> None:
    async def gen(state: Any, tools: Any) -> list[str]:
        return ["x"]  # always 1 action

    async def transition(state: Any, action: str) -> dict[str, Any]:
        return {"n": state.get("n", 0) + 1}

    reward_fn = AsyncMock(return_value=0.5)

    agent = LATSAgent(
        tools=[],
        reward_fn=reward_fn,
        action_generator=gen,
        transition_fn=transition,
        max_simulations=7,
        max_depth=10,
        settings=MagicMock(),
    )
    result = await agent.search(initial_state={"n": 0}, goal="g")
    assert result.total_simulations == 7
    # reward_fn called at most max_simulations times (once per simulation)
    assert reward_fn.call_count <= 7


@pytest.mark.asyncio
async def test_search_respects_max_depth() -> None:
    """Tree never exceeds max_depth, so path length is bounded."""

    async def gen(state: Any, tools: Any) -> list[str]:
        return ["go"]

    async def transition(state: Any, action: str) -> dict[str, Any]:
        return {"d": state.get("d", 0) + 1}

    agent = LATSAgent(
        tools=[],
        reward_fn=AsyncMock(return_value=0.1),
        action_generator=gen,
        transition_fn=transition,
        max_simulations=50,
        max_depth=3,
        settings=MagicMock(),
    )
    result = await agent.search(initial_state={"d": 0}, goal="g")
    assert len(result.best_action_sequence) <= 3
    assert result.search_tree_depth <= 3


@pytest.mark.asyncio
async def test_search_times_out_when_budget_elapses() -> None:
    """timeout_seconds caps wall-clock; loop exits before max_simulations."""

    async def slow_reward(state: Any) -> float:
        await asyncio.sleep(0.05)
        return 0.1

    async def gen(state: Any, tools: Any) -> list[str]:
        return ["x"]

    async def transition(state: Any, action: str) -> dict[str, Any]:
        return {}

    agent = LATSAgent(
        tools=[],
        reward_fn=slow_reward,
        action_generator=gen,
        transition_fn=transition,
        max_simulations=1000,
        timeout_seconds=0.1,
        settings=MagicMock(),
    )
    result = await agent.search(initial_state={}, goal="g")
    # 0.1s budget / 0.05s per reward = roughly 2-3 simulations max
    assert result.total_simulations < 1000


@pytest.mark.asyncio
async def test_search_raises_when_no_simulation_produces_reward() -> None:
    """If ALL simulations return 0 and no children are produced, raise LATSError."""

    async def no_actions(state: Any, tools: Any) -> list[str]:
        return []

    async def transition(state: Any, action: str) -> dict[str, Any]:
        return state

    agent = LATSAgent(
        tools=[],
        reward_fn=AsyncMock(return_value=0.0),
        action_generator=no_actions,
        transition_fn=transition,
        max_simulations=5,
        settings=MagicMock(),
    )
    with pytest.raises(LATSError):
        await agent.search(initial_state={}, goal="g")


# ── Exploration / exploitation balance ────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_ultimately_favours_higher_reward_path() -> None:
    """Given enough simulations, MCTS converges to the better branch."""

    async def gen(state: Any, tools: Any) -> list[str]:
        if state.get("depth", 0) >= 1:
            return []
        return ["good", "bad"]

    async def transition(state: Any, action: str) -> dict[str, Any]:
        return {"depth": state.get("depth", 0) + 1, "tag": action}

    async def reward(state: Any) -> float:
        return 0.95 if state.get("tag") == "good" else 0.05

    agent = LATSAgent(
        tools=[],
        reward_fn=reward,
        action_generator=gen,
        transition_fn=transition,
        max_simulations=30,
        max_depth=1,
        settings=MagicMock(),
    )
    result = await agent.search(initial_state={"depth": 0}, goal="g")
    # Best path should start with "good"
    assert result.best_action_sequence[0] == "good"
    assert result.best_reward >= 0.9
