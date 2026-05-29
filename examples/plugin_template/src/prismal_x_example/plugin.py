"""Subgraph registration entry point (``prismal.subgraphs`` group)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from prismal.agents.extension import PrismalStateGraphBuilder

if TYPE_CHECKING:
    from prismal.agents.subgraphs.registry import SubgraphRegistry


async def _intake(state: dict) -> dict:
    return {"metadata": {"example_intake": True}}


async def _respond(state: dict) -> dict:
    return {"metadata": {"example_respond": True}}


def register_example_pipeline(registry: SubgraphRegistry) -> None:
    """Build and self-register the example subgraph.

    discover_plugins() calls this with the target registry. Either
    self-register via ``registry.register_sync(...)`` (shown here) or return a
    ``SubgraphDefinition`` for the discoverer to register.
    """
    builder = PrismalStateGraphBuilder("example_pipeline")
    builder.add_node("intake", _intake)
    builder.add_node("respond", _respond)
    builder.add_edge("intake", "respond")
    builder.add_edge("respond", "__end__")
    builder.set_entry_point("intake")
    registry.register_sync("example_pipeline", builder.compile())
