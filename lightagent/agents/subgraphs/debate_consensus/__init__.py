"""Debate/Consensus subgraph (C5).

Pipeline: proponent → opponent → moderator → consensus.

Thin LangGraph wrapper around the debate primitives from
:mod:`lightagent.agents.patterns.debate` (SPEC-PAT-002). Each debate role
becomes a node; the terminal ``consensus`` node synthesises a final answer
and records the Jaccard agreement score.

Public entry points::

    from lightagent.agents.subgraphs.debate_consensus import (
        build_debate_consensus_subgraph,
        DebateConsensusError,
    )
"""

from __future__ import annotations

from lightagent.agents.subgraphs.debate_consensus.builder import (
    build_debate_consensus_subgraph,
)
from lightagent.agents.subgraphs.debate_consensus.consensus_node import (
    make_consensus_node,
)
from lightagent.agents.subgraphs.debate_consensus.moderator_node import (
    make_moderator_node,
)
from lightagent.agents.subgraphs.debate_consensus.opponent_node import (
    make_opponent_node,
)
from lightagent.agents.subgraphs.debate_consensus.proponent_node import (
    make_proponent_node,
)
from lightagent.core.exceptions import DebateConsensusError

__all__ = [
    "DebateConsensusError",
    "build_debate_consensus_subgraph",
    "make_consensus_node",
    "make_moderator_node",
    "make_opponent_node",
    "make_proponent_node",
]
