"""Unit tests for debate_consensus subgraph (C5).

Pipeline::

    proponent → opponent → moderator → consensus

- proponent_node: LLM produces a supporting stance.
- opponent_node: LLM produces a contrary stance, sees prior positions.
- moderator_node: LLM produces a neutral mediator position.
- consensus_node: synthesises a final consensus using all three positions
  and computes the Jaccard-based agreement score (reused from B2).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from prismal.agents.patterns.debate import DebatePosition
from prismal.agents.subgraphs.debate_consensus import (
    DebateConsensusError,
    make_consensus_node,
    make_moderator_node,
    make_opponent_node,
    make_proponent_node,
)
from prismal.agents.subgraphs.debate_consensus.builder import (
    build_debate_consensus_subgraph,
)
from prismal.core.exceptions import LightAgentError

# ── Helpers ───────────────────────────────────────────────────────────────────


def _state(query: str = "Should we refactor the supervisor?") -> dict[str, Any]:
    return {
        "messages": [HumanMessage(content=query)],
        "metadata": {},
    }


def _llm_returning(*texts: str) -> MagicMock:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=[MagicMock(content=t) for t in texts])
    return llm


# ── Exception ─────────────────────────────────────────────────────────────────


def test_debate_consensus_error_inherits_from_lightagent_error() -> None:
    assert issubclass(DebateConsensusError, LightAgentError)


# ── proponent_node ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_proponent_records_position_with_role_proponent() -> None:
    llm = _llm_returning("Yes — we should proceed because …")
    node = make_proponent_node(llm=llm)
    update = await node(_state())
    positions = update["metadata"]["debate_consensus"]["positions"]
    assert len(positions) == 1
    p = positions[0]
    assert isinstance(p, DebatePosition)
    assert p.role == "proponent"
    assert p.round == 1
    assert "proceed" in p.content.lower()


@pytest.mark.asyncio
async def test_proponent_noop_on_empty_messages() -> None:
    llm = _llm_returning("x")
    node = make_proponent_node(llm=llm)
    update = await node({"messages": [], "metadata": {}})
    assert update == {}
    llm.ainvoke.assert_not_called()


# ── opponent_node ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_opponent_sees_proponent_position_in_prompt() -> None:
    llm = _llm_returning("No — refactor is premature because …")
    node = make_opponent_node(llm=llm)
    state = _state()
    state["metadata"]["debate_consensus"] = {
        "positions": [
            DebatePosition(
                agent_id="agent_0",
                role="proponent",
                content="PROPONENT-ARGUMENT-TEXT",
                round=1,
            )
        ]
    }
    await node(state)
    # opponent's user prompt should include proponent's text
    call = llm.ainvoke.call_args.args[0]
    user_content = call[1].content
    assert "PROPONENT-ARGUMENT-TEXT" in user_content


@pytest.mark.asyncio
async def test_opponent_appends_its_position_after_proponent() -> None:
    llm = _llm_returning("opponent stance")
    node = make_opponent_node(llm=llm)
    state = _state()
    state["metadata"]["debate_consensus"] = {
        "positions": [
            DebatePosition(
                agent_id="agent_0",
                role="proponent",
                content="p",
                round=1,
            )
        ]
    }
    update = await node(state)
    positions = update["metadata"]["debate_consensus"]["positions"]
    assert len(positions) == 2
    assert positions[1].role == "opponent"


# ── moderator_node ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_moderator_sees_all_prior_positions() -> None:
    llm = _llm_returning("moderator neutral view")
    node = make_moderator_node(llm=llm)
    state = _state()
    state["metadata"]["debate_consensus"] = {
        "positions": [
            DebatePosition(agent_id="a0", role="proponent", content="PROP", round=1),
            DebatePosition(agent_id="a1", role="opponent", content="OPP", round=1),
        ]
    }
    await node(state)
    user_content = llm.ainvoke.call_args.args[0][1].content
    assert "PROP" in user_content
    assert "OPP" in user_content


@pytest.mark.asyncio
async def test_moderator_appends_its_position() -> None:
    llm = _llm_returning("balanced perspective")
    node = make_moderator_node(llm=llm)
    state = _state()
    state["metadata"]["debate_consensus"] = {
        "positions": [
            DebatePosition(agent_id="a0", role="proponent", content="p", round=1),
            DebatePosition(agent_id="a1", role="opponent", content="o", round=1),
        ]
    }
    update = await node(state)
    positions = update["metadata"]["debate_consensus"]["positions"]
    assert len(positions) == 3
    assert positions[2].role == "moderator"


# ── consensus_node ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_consensus_synthesises_final_answer_and_agreement_score() -> None:
    llm = _llm_returning("SYNTHESIS: a balanced consensus decision.")
    node = make_consensus_node(llm=llm)
    state = _state()
    state["metadata"]["debate_consensus"] = {
        "positions": [
            DebatePosition(agent_id="a0", role="proponent", content="we should ship now", round=1),
            DebatePosition(agent_id="a1", role="opponent", content="we should wait", round=1),
            DebatePosition(
                agent_id="a2", role="moderator", content="ship with feature flag", round=1
            ),
        ]
    }
    update = await node(state)
    dc = update["metadata"]["debate_consensus"]
    assert "balanced consensus" in dc["consensus"]
    assert 0.0 <= dc["agreement_score"] <= 1.0
    # AIMessage with the consensus is appended
    assert isinstance(update["messages"][0], AIMessage)
    assert "balanced consensus" in update["messages"][0].content


@pytest.mark.asyncio
async def test_consensus_noop_when_no_positions() -> None:
    llm = _llm_returning("unused")
    node = make_consensus_node(llm=llm)
    state = _state()
    state["metadata"]["debate_consensus"] = {"positions": []}
    update = await node(state)
    assert update == {}
    llm.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_consensus_agreement_high_for_similar_positions() -> None:
    """Identical position texts → agreement_score near 1.0."""
    llm = _llm_returning("consensus")
    node = make_consensus_node(llm=llm)
    state = _state()
    state["metadata"]["debate_consensus"] = {
        "positions": [
            DebatePosition(agent_id=f"a{i}", role="r", content="same text", round=1)
            for i in range(3)
        ]
    }
    update = await node(state)
    assert update["metadata"]["debate_consensus"]["agreement_score"] > 0.9


# ── builder ──────────────────────────────────────────────────────────────────


def test_build_debate_consensus_subgraph_has_four_nodes() -> None:
    defn = build_debate_consensus_subgraph(llm=_llm_returning("x"))
    assert set(defn.nodes.keys()) == {
        "proponent",
        "opponent",
        "moderator",
        "consensus",
    }


def test_build_debate_consensus_subgraph_entry_point_is_proponent() -> None:
    defn = build_debate_consensus_subgraph(llm=_llm_returning("x"))
    assert defn.entry_point == "proponent"


def test_build_debate_consensus_subgraph_linear_edges() -> None:
    defn = build_debate_consensus_subgraph(llm=_llm_returning("x"))
    edges = set(defn.edges)
    expected = {
        ("proponent", "opponent"),
        ("opponent", "moderator"),
        ("moderator", "consensus"),
    }
    assert expected.issubset(edges)
