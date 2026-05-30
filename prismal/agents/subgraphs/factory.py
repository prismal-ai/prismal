"""SubgraphFactory: builds compiled LangGraph subgraphs from definitions.

Each subgraph is a self-contained ``CompiledStateGraph`` that can be added as
a node to the main supervisor graph via ``builder.add_node(name, compiled)``.

Checkpointing is isolated per-subgraph to prevent state contention.  Pass
``checkpointer_path=":memory:"`` in unit tests; production uses
``data/db/checkpoints_subgraph_{name}.db``.

Usage::

    factory = SubgraphFactory()
    compiled = await factory.build(definition, checkpointer_path=":memory:")
    builder.add_node("dev_pipeline", compiled)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import aiosqlite
import structlog
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.constants import END
from langgraph.graph import StateGraph

from prismal.agents.state import AgentState

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

    from prismal.agents.subgraphs.registry import SubgraphDefinition

logger = structlog.get_logger("prismal.subgraphs.factory")


def assemble_state_graph(
    definition: SubgraphDefinition,
) -> StateGraph[AgentState, Any, Any, Any]:
    """Assemble (but do not compile) a ``StateGraph`` from a definition.

    Registers nodes, the entry point, linear edges, conditional edges and
    Send-based fan-out edges, then wires terminal nodes to ``END``. Shared by
    :meth:`SubgraphFactory.build` (which adds a checkpointer and compiles) and
    the visualization helpers (which compile without a checkpointer). No
    checkpointer, no I/O — pure, synchronous assembly.

    Args:
        definition: The :class:`SubgraphDefinition` to assemble.

    Returns:
        An uncompiled ``StateGraph`` mirroring the definition's topology.
    """
    builder: StateGraph[AgentState, Any, Any, Any] = StateGraph(AgentState)

    for node_name, node_fn in definition.nodes.items():
        builder.add_node(node_name, node_fn)

    builder.set_entry_point(definition.entry_point)

    for from_node, to_node in definition.edges:
        builder.add_edge(from_node, END if to_node == "__end__" else to_node)

    for source_node, routing_fn in definition.conditional_edges.items():
        builder.add_conditional_edges(source_node, routing_fn)

    # Phase 34: Send-based fan-out edges — dispatcher returns list[Send] or a
    # fallback node name; both are valid ``add_conditional_edges`` returns.
    for source_node, dispatcher_fn in definition.send_edges:
        builder.add_conditional_edges(source_node, dispatcher_fn)

    # Nodes with no outgoing edge of any kind terminate at END.
    all_sources = (
        {f for f, _ in definition.edges}
        | set(definition.conditional_edges)
        | {src for src, _ in definition.send_edges}
    )
    for node_name in definition.nodes:
        if node_name not in all_sources:
            builder.add_edge(node_name, END)

    return builder


class SubgraphFactory:
    """Builds compiled LangGraph subgraphs from :class:`SubgraphDefinition` objects.

    Each call to :meth:`build` produces an independent ``CompiledStateGraph``
    with its own
    :class:`~langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver`
    checkpointer — subgraphs never share checkpoint storage.

    :meth:`build` is an async method because opening the ``aiosqlite``
    connection and calling ``checkpointer.setup()`` are async operations.

    Example::

        factory = SubgraphFactory()
        graph = await factory.build(
            defn,
            checkpointer_path="data/db/checkpoints_subgraph_dev.db",
        )
    """

    async def build(
        self,
        definition: SubgraphDefinition,
        checkpointer_path: str | None = ":memory:",
    ) -> CompiledStateGraph[AgentState, Any, Any, Any]:
        """Compile a subgraph from a definition.

        Registers all nodes, adds linear edges, adds conditional edges,
        and compiles with an isolated checkpointer.

        When ``checkpointer_path`` is an explicit string (the current
        default ``":memory:"`` or a dedicated file path such as
        ``data/db/checkpoints_subgraph_dev.db``) the factory creates an
        :class:`~langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver` bound
        to that path — this preserves the pre-Phase-36 behaviour where
        every subgraph owns an isolated SQLite checkpoint file.

        When ``checkpointer_path`` is ``None`` the factory delegates to
        :func:`prismal.agents.graph.build_checkpointer` which selects
        the backend from ``settings.db_url`` (SQLite or PostgreSQL).
        This is required for production multi-worker deployments where
        HITL workflows must be resumable across processes (AC-038-3).

        Args:
            definition: The :class:`SubgraphDefinition` specifying
                nodes and edges.
            checkpointer_path: SQLite path for checkpointing. Use
                ``":memory:"`` in tests; pass a dedicated file path for
                the legacy isolated-per-subgraph behaviour, or ``None``
                to use the globally-configured checkpointer backend.

        Returns:
            A compiled ``CompiledStateGraph`` ready to be used as a
            node.
        """
        builder = assemble_state_graph(definition)

        if checkpointer_path is None:
            # Phase 36: delegate to the global checkpointer factory so the
            # backend is driven by ``settings.db_url`` (SQLite or Postgres).
            # Import lazily to avoid a circular import at module load time.
            from prismal.agents.graph import build_checkpointer

            checkpointer = await build_checkpointer()
            checkpointer_label = "build_checkpointer(settings.db_url)"
        else:
            conn = await aiosqlite.connect(checkpointer_path)
            checkpointer = AsyncSqliteSaver(conn)
            await checkpointer.setup()
            checkpointer_label = checkpointer_path

        compiled: CompiledStateGraph[AgentState, Any, Any, Any] = builder.compile(
            checkpointer=checkpointer
        )
        logger.info(
            "subgraph.compiled",
            name=definition.name,
            node_count=len(definition.nodes),
            checkpointer=checkpointer_label,
        )
        return compiled


__all__ = ["SubgraphFactory", "assemble_state_graph"]
