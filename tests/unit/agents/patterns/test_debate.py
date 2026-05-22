"""Unit tests for debate_round (prismal.agents.patterns.debate).

Debate pattern: N agents with distinct roles produce positions on the query,
optionally refine them across rounds, then a synthesis step produces a
consensus plus an agreement score.

LLM is mocked so no real provider calls happen.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prismal.agents.patterns.debate import (
    DebateError,
    DebatePosition,
    DebateResult,
    debate_round,
)
from prismal.core.exceptions import PrismalError

PROVIDER_REGISTRY_PATH = "prismal.agents.patterns.debate.ProviderRegistry"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _response(text: str) -> MagicMock:
    r = MagicMock()
    r.content = text
    return r


def _mock_llm(*texts: str) -> MagicMock:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=[_response(t) for t in texts])
    return llm


def _state() -> Any:
    return {}


# ── Dataclass / exception ─────────────────────────────────────────────────────


def test_debate_position_is_instantiable() -> None:
    p = DebatePosition(agent_id="a0", role="proponent", content="X is true", round=1)
    assert p.agent_id == "a0"
    assert p.role == "proponent"
    assert p.content == "X is true"
    assert p.round == 1


def test_debate_result_is_instantiable() -> None:
    p = DebatePosition(agent_id="a0", role="r", content="c", round=1)
    r = DebateResult(
        consensus="summary",
        agreement_score=0.7,
        positions=[p],
        dissenting_views=[],
        rounds_completed=1,
    )
    assert r.consensus == "summary"
    assert r.agreement_score == 0.7
    assert r.rounds_completed == 1


def test_debate_error_inherits_from_prismal_error() -> None:
    assert issubclass(DebateError, PrismalError)


# ── Input validation ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_invalid_n_agents_raises() -> None:
    with pytest.raises(ValueError):
        await debate_round(
            query="q",
            state=_state(),
            n_agents=0,
            settings=MagicMock(),
        )


@pytest.mark.asyncio
async def test_invalid_n_rounds_raises() -> None:
    with pytest.raises(ValueError):
        await debate_round(
            query="q",
            state=_state(),
            n_rounds=0,
            settings=MagicMock(),
        )


# ── Basic flow ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_debate_produces_n_agents_times_n_rounds_positions() -> None:
    # 3 agents × 2 rounds = 6 positions, + 1 LLM call for moderator synthesis
    texts = [
        # Round 1: 3 initial positions
        "Proponent says yes.",
        "Opponent says no.",
        "Neutral is unsure.",
        # Round 2: 3 revised positions
        "Proponent insists yes.",
        "Opponent doubles down: no.",
        "Neutral leans yes.",
        # Synthesis
        "The consensus: yes, with caveats.",
    ]
    llm = _mock_llm(*texts)
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = llm
        result = await debate_round(
            query="Should we adopt X?",
            state=_state(),
            n_agents=3,
            n_rounds=2,
            settings=MagicMock(),
        )

    assert len(result.positions) == 6
    assert result.rounds_completed == 2
    assert result.consensus == "The consensus: yes, with caveats."
    # agreement_score is numeric in [0, 1]
    assert 0.0 <= result.agreement_score <= 1.0


@pytest.mark.asyncio
async def test_consensus_is_not_a_verbatim_copy_of_any_position() -> None:
    texts = [
        "Position A text.",
        "Position B text.",
        "Position C text.",
        # Synthesis: different from any single position
        "A synthesis combining A, B, and C.",
    ]
    llm = _mock_llm(*texts)
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = llm
        result = await debate_round(
            query="q",
            state=_state(),
            n_agents=3,
            n_rounds=1,
            settings=MagicMock(),
        )
    for p in result.positions:
        assert result.consensus != p.content


@pytest.mark.asyncio
async def test_custom_roles_are_respected() -> None:
    texts = ["skeptic view", "advocate view", "synthesis"]
    llm = _mock_llm(*texts)
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = llm
        result = await debate_round(
            query="q",
            state=_state(),
            n_agents=2,
            n_rounds=1,
            roles=["skeptic", "advocate"],
            settings=MagicMock(),
        )
    roles_used = {p.role for p in result.positions}
    assert roles_used == {"skeptic", "advocate"}


@pytest.mark.asyncio
async def test_subsequent_rounds_see_previous_positions() -> None:
    """Round 2 prompts must include round 1 content."""
    texts = ["R1-A", "R1-B", "R2-A", "R2-B", "consensus"]
    llm = _mock_llm(*texts)
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = llm
        await debate_round(
            query="q",
            state=_state(),
            n_agents=2,
            n_rounds=2,
            settings=MagicMock(),
        )

    # Round 2 agent prompts should contain R1-A or R1-B (prior positions).
    # ainvoke call args: agent prompts 1..2 (round 1), 3..4 (round 2), 5 (synthesis).
    # Check calls 3 and 4 have round-1 content.
    round2_calls = llm.ainvoke.call_args_list[2:4]
    combined = " ".join(
        str(call.args[0][1].content)
        for call in round2_calls  # HumanMessage
    )
    assert "R1-A" in combined or "R1-B" in combined


# ── Synthesis strategies ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_majority_vote_synthesis_skips_moderator_llm() -> None:
    """With majority_vote, no synthesis LLM call is made — 3 position calls only."""
    texts = ["yes", "yes", "no"]  # 2 "yes" wins
    llm = _mock_llm(*texts)
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = llm
        result = await debate_round(
            query="q",
            state=_state(),
            n_agents=3,
            n_rounds=1,
            synthesis_strategy="majority_vote",
            settings=MagicMock(),
        )
    assert llm.ainvoke.call_count == 3
    assert result.consensus == "yes"


@pytest.mark.asyncio
async def test_moderator_synthesis_invokes_extra_llm_call() -> None:
    texts = ["pos1", "pos2", "moderated consensus"]
    llm = _mock_llm(*texts)
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = llm
        result = await debate_round(
            query="q",
            state=_state(),
            n_agents=2,
            n_rounds=1,
            synthesis_strategy="moderator",
            settings=MagicMock(),
        )
    # 2 positions + 1 moderator = 3 LLM calls
    assert llm.ainvoke.call_count == 3
    assert result.consensus == "moderated consensus"


# ── Agreement score ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_agreement_score_is_high_for_similar_positions() -> None:
    """Identical positions should produce agreement_score close to 1.0."""
    texts = ["same text", "same text", "same text", "consensus"]
    llm = _mock_llm(*texts)
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = llm
        result = await debate_round(
            query="q",
            state=_state(),
            n_agents=3,
            n_rounds=1,
            settings=MagicMock(),
        )
    assert result.agreement_score > 0.9


@pytest.mark.asyncio
async def test_agreement_score_is_low_for_disparate_positions() -> None:
    """Totally different positions should produce low agreement_score."""
    texts = ["alpha beta", "gamma delta", "epsilon zeta", "consensus"]
    llm = _mock_llm(*texts)
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = llm
        result = await debate_round(
            query="q",
            state=_state(),
            n_agents=3,
            n_rounds=1,
            settings=MagicMock(),
        )
    assert result.agreement_score < 0.3


# ── Error handling ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_raises_debate_error_when_all_agents_fail() -> None:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=RuntimeError("boom"))
    with patch(PROVIDER_REGISTRY_PATH) as reg:
        reg.return_value.get_llm.return_value = llm
        with pytest.raises(DebateError):
            await debate_round(
                query="q",
                state=_state(),
                n_agents=3,
                n_rounds=1,
                settings=MagicMock(),
            )
