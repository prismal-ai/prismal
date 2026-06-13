"""Budget-guard integration in the expensive patterns (Phase C — C6-03..07).

Each pattern accepts an optional ``budget_guard_fn`` checked before each
expansion unit (round / level / simulation / iteration / layer). When it returns
False the pattern stops expanding and returns best effort; ``None`` leaves the
pattern unchanged. The fn is the one produced by ``make_budget_guard_fn``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from prismal.agents.patterns.debate import debate_round
from prismal.agents.patterns.lats import LATSAgent
from prismal.agents.patterns.mixture_of_agents import MixtureOfAgents
from prismal.agents.patterns.reflection import reflection_loop
from prismal.agents.patterns.tree_of_thoughts import tree_of_thoughts


def _always_block() -> Any:
    """A budget_guard_fn that always says 'do not proceed' (soft degrade)."""

    async def _fn(_ctx: dict[str, object]) -> bool:
        return False

    return _fn


def _block_after(n: int) -> Any:
    """A budget_guard_fn that proceeds for *n* checks then blocks."""
    calls = {"c": 0}

    async def _fn(_ctx: dict[str, object]) -> bool:
        calls["c"] += 1
        return calls["c"] <= n

    return _fn


# ── reflection_loop (C6-07) ───────────────────────────────────────────────────


async def test_reflection_stops_on_budget() -> None:
    calls = {"gen": 0}

    async def gen(state: Any, **kw: Any) -> str:
        calls["gen"] += 1
        return "draft"

    async def crit(draft: str, state: Any) -> tuple[str, float]:
        return ("needs work", 0.1)  # always below threshold → would iterate

    await reflection_loop(
        gen, crit, {}, threshold=0.9, max_iterations=3, budget_guard_fn=_always_block()
    )
    assert calls["gen"] == 1  # iteration 1 generates; iteration 2 is blocked


async def test_reflection_unchanged_without_guard() -> None:
    calls = {"gen": 0}

    async def gen(state: Any, **kw: Any) -> str:
        calls["gen"] += 1
        return "draft"

    async def crit(draft: str, state: Any) -> tuple[str, float]:
        return ("needs work", 0.1)

    await reflection_loop(gen, crit, {}, threshold=0.9, max_iterations=3)
    assert calls["gen"] == 3  # full iterations when no guard


# ── tree_of_thoughts (C6-04) ──────────────────────────────────────────────────


async def test_tot_stops_deepening_on_budget() -> None:
    gen_calls = {"n": 0}

    async def gen(problem: str, state: Any, path: Any) -> list[str]:
        gen_calls["n"] += 1
        return ["a", "b"]

    async def evaluate(text: str, state: Any) -> float:
        return 0.5

    result = await tree_of_thoughts(
        "p",
        gen,
        evaluate,
        {},
        breadth=2,
        depth=3,
        beam_size=2,
        budget_guard_fn=_always_block(),
    )
    # Depth 1 expands (root), then the budget blocks deeper levels.
    assert max(t.depth for t in result.all_thoughts) == 1


async def test_tot_unchanged_without_guard() -> None:
    async def gen(problem: str, state: Any, path: Any) -> list[str]:
        return ["a", "b"]

    async def evaluate(text: str, state: Any) -> float:
        return 0.5

    result = await tree_of_thoughts("p", gen, evaluate, {}, breadth=2, depth=3, beam_size=2)
    assert max(t.depth for t in result.all_thoughts) == 3


# ── LATS (C6-05) ──────────────────────────────────────────────────────────────


def _lats_agent(max_sims: int = 10) -> LATSAgent:
    async def reward(state: Any) -> float:
        return 0.2

    async def actions(state: Any, tools: Any) -> list[str]:
        return ["x"]

    async def transition(state: Any, action: Any) -> str:
        return "s2"

    return LATSAgent(
        tools=[],
        reward_fn=reward,
        action_generator=actions,
        transition_fn=transition,
        max_simulations=max_sims,
        settings=MagicMock(),
    )


async def test_lats_stops_on_budget() -> None:
    agent = _lats_agent(max_sims=10)
    # Allow two simulations (enough to expand children), then the budget stops.
    result = await agent.search("s0", "goal", budget_guard_fn=_block_after(1))
    assert result.total_simulations == 2
    assert result.total_simulations < 10


async def test_lats_unchanged_without_guard() -> None:
    agent = _lats_agent(max_sims=5)
    result = await agent.search("s0", "goal")
    assert result.total_simulations == 5


# ── debate_round (C6-03) ──────────────────────────────────────────────────────


def _mock_response(text: str) -> MagicMock:
    r = MagicMock()
    r.content = text
    return r


async def test_debate_stops_rounds_on_budget() -> None:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=_mock_response("position"))
    with patch("prismal.agents.patterns.debate.ProviderRegistry") as reg:
        reg.return_value.get_llm.return_value = llm
        result = await debate_round(
            "q",
            {},
            n_agents=2,
            n_rounds=3,
            settings=MagicMock(),
            budget_guard_fn=_always_block(),
        )
    # Only round 1 positions exist — rounds 2 and 3 were budget-blocked.
    assert max(p.round for p in result.positions) == 1


async def test_debate_unchanged_without_guard() -> None:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=_mock_response("position"))
    with patch("prismal.agents.patterns.debate.ProviderRegistry") as reg:
        reg.return_value.get_llm.return_value = llm
        result = await debate_round("q", {}, n_agents=2, n_rounds=2, settings=MagicMock())
    assert max(p.round for p in result.positions) == 2


# ── MixtureOfAgents (C6-06) ───────────────────────────────────────────────────


async def test_moa_stops_aggregator_layers_on_budget() -> None:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=_mock_response("out"))
    with patch("prismal.agents.patterns.mixture_of_agents.ProviderRegistry") as reg:
        reg.return_value.get_llm.return_value = llm
        moa = MixtureOfAgents(
            proposer_models=["m1", "m2"], n_aggregator_layers=3, settings=MagicMock()
        )
        result = await moa.generate("q", {}, budget_guard_fn=_always_block())
    # No aggregator layer runs: only the proposer layer is present.
    assert len(result.layer_outputs) == 1
