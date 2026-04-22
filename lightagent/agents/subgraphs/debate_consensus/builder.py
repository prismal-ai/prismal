"""Builder for the debate_consensus subgraph (C5).

Linear pipeline::

    proponent → opponent → moderator → consensus

Reuses the ``DebatePosition`` / ``pairwise_jaccard`` primitives from
:mod:`lightagent.agents.patterns.debate` (SPEC-PAT-002). Each node produces
one :class:`DebatePosition`; the terminal ``consensus`` node synthesises
them into one final answer and records the Jaccard agreement score.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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
from lightagent.agents.subgraphs.registry import SubgraphDefinition
from lightagent.core.logging import get_logger

if TYPE_CHECKING:
    from lightagent.core.config import Settings

logger = get_logger("lightagent.subgraphs.debate_consensus.builder")

_NAME = "debate_consensus"
_DESCRIPTION = "Debate/consensus pipeline: proponent → opponent → moderator → consensus"


def build_debate_consensus_subgraph(
    llm: Any | None = None,
    settings: Settings | None = None,
) -> SubgraphDefinition:
    """Build the debate_consensus :class:`SubgraphDefinition`.

    Args:
        llm: Pre-built LLM handle shared by all four role nodes. ``None``
            resolves via :class:`ProviderRegistry`.
        settings: Reserved for future auto-wiring.

    Returns:
        :class:`SubgraphDefinition` with 4 nodes and linear edges.
    """
    del settings

    if llm is None:
        from lightagent.providers.registry import ProviderRegistry

        llm = ProviderRegistry().get_llm()

    proponent = make_proponent_node(llm=llm)
    opponent = make_opponent_node(llm=llm)
    moderator = make_moderator_node(llm=llm)
    consensus = make_consensus_node(llm=llm)

    definition = SubgraphDefinition(
        name=_NAME,
        description=_DESCRIPTION,
        entry_point="proponent",
        nodes={
            "proponent": proponent,
            "opponent": opponent,
            "moderator": moderator,
            "consensus": consensus,
        },
        edges=[
            ("proponent", "opponent"),
            ("opponent", "moderator"),
            ("moderator", "consensus"),
        ],
    )
    logger.info(
        "debate_consensus_subgraph_built",
        nodes=list(definition.nodes.keys()),
    )
    return definition


async def register_debate_consensus(
    llm: Any | None = None,
    settings: Settings | None = None,
) -> None:
    """Register the debate_consensus subgraph (idempotent)."""
    from lightagent.agents.subgraphs.registry import SubgraphRegistry

    registry = SubgraphRegistry.get_instance()
    if registry.get(_NAME) is not None:
        logger.info("debate_consensus.already_registered")
        return
    definition = build_debate_consensus_subgraph(llm=llm, settings=settings)
    await registry.register(_NAME, definition)
    logger.info("debate_consensus.registered")


__all__ = ["build_debate_consensus_subgraph", "register_debate_consensus"]
