"""Builder for the ``research_orchestrator`` domain subgraph.

SPEC-042 AC-042-1 — the research domain aggregates every leaf agent
whose job is to *gather information*: web research, internal RAG Q&A,
and (eventually) a Computer Use Agent for interactive UI scraping. In
hierarchical mode (``PRISMAL_HIERARCHICAL_MODE=true``) the root
supervisor delegates any investigation-flavoured request to this
subgraph instead of picking individual leaves.

Topology
--------

::

    research_supervisor ──┬──► researcher ──┐
                          ├──► rag_agent  ──┼──► research_supervisor (loop-break → END)
                          └──► __end__

The entry point is the ``research_supervisor`` node produced by
:func:`~prismal.agents.domain_supervisor.make_domain_supervisor`.
Its built-in loop breaker guarantees that a single specialist
response terminates the subgraph after at most two supervisor calls
per invocation, so the routing conditional edge is a thin wrapper
that reads ``state["next_agent"]`` and dispatches directly.
"""

from __future__ import annotations

from typing import Any

import structlog

from prismal.agents.domain_supervisor import make_domain_supervisor
from prismal.agents.rag_agent import rag_agent_node
from prismal.agents.researcher import researcher_node
from prismal.agents.subgraphs.factory import SubgraphFactory
from prismal.agents.subgraphs.registry import (
    SubgraphDefinition,
    SubgraphRegistry,
)

logger = structlog.get_logger("prismal.subgraphs.research_orchestrator.builder")

_NAME = "research_orchestrator"
_DESCRIPTION = (
    "Research domain orchestrator — routes investigation, web "
    "lookup, and internal knowledge-base questions between "
    "researcher and rag_agent."
)
_SUPERVISOR_DESCRIPTION = (
    "Handle requests that require gathering information from the web, "
    "internal documentation, or external knowledge sources. Typical "
    "tasks: factual lookups, literature surveys, internal document "
    "Q&A, summarising papers, comparing options."
)

#: Leaf agent names the research domain is allowed to route to. Keeping
#: this as a module-level tuple makes it trivial for tests to assert
#: "the root supervisor should never route to agents outside this set"
#: and for the domain supervisor system prompt to enumerate them.
RESEARCH_AGENTS: tuple[str, ...] = ("researcher", "rag_agent")

# Module-level compiled-graph cache so the orchestrator is built once
# per process. Mirrors the pattern used by the financial / dev_pipeline
# builders.
_COMPILED_GRAPHS: dict[str, object] = {}


def _research_router(state: dict[str, Any]) -> str:
    """Conditional-edge router for ``research_supervisor``.

    Reads ``state["next_agent"]`` (written by the domain supervisor) and
    maps it to either a leaf node name or the LangGraph ``__end__``
    sentinel. Unknown values — including the case where the supervisor
    somehow returned a leaf from *another* domain — default to
    ``__end__`` so execution always terminates cleanly inside the
    subgraph.
    """
    next_agent = state.get("next_agent")
    if next_agent is None or next_agent == "END":
        return "__end__"
    if next_agent in RESEARCH_AGENTS:
        return str(next_agent)
    logger.warning(
        "research_orchestrator.router_unknown_next_agent",
        next_agent=next_agent,
    )
    return "__end__"


def _make_definition() -> SubgraphDefinition:
    """Build the :class:`SubgraphDefinition` for the research domain.

    Returns:
        A fully configured :class:`SubgraphDefinition` ready to be
        compiled with :meth:`SubgraphFactory.build`.
    """
    supervisor_node = make_domain_supervisor(
        domain_name="research",
        agents=list(RESEARCH_AGENTS),
        domain_description=_SUPERVISOR_DESCRIPTION,
    )
    return SubgraphDefinition(
        name=_NAME,
        description=_DESCRIPTION,
        entry_point="research_supervisor",
        nodes={
            "research_supervisor": supervisor_node,
            "researcher": researcher_node,
            "rag_agent": rag_agent_node,
        },
        edges=[
            # Every leaf returns to the domain supervisor so its
            # built-in loop breaker can terminate the turn.
            ("researcher", "research_supervisor"),
            ("rag_agent", "research_supervisor"),
        ],
        conditional_edges={
            "research_supervisor": _research_router,
        },
    )


async def register_research_orchestrator(
    checkpointer_path: str = ("data/db/checkpoints_subgraph_research_orchestrator.db"),
) -> None:
    """Compile and register the research orchestrator.

    Idempotent — skips registration if already present in the global
    :class:`SubgraphRegistry`. Safe to call from multiple places (e.g.
    startup hooks, test fixtures).

    Args:
        checkpointer_path: SQLite file path for the subgraph's isolated
            checkpointer. Use ``":memory:"`` in tests.
    """
    registry = SubgraphRegistry.get_instance()
    if registry.get(_NAME) is not None:
        logger.info("research_orchestrator.already_registered")
        return

    definition = _make_definition()
    factory = SubgraphFactory()
    compiled = await factory.build(definition, checkpointer_path=checkpointer_path)
    await registry.register(_NAME, definition)

    _COMPILED_GRAPHS[_NAME] = compiled
    logger.info("research_orchestrator.registered")


async def get_compiled_research_orchestrator(
    checkpointer_path: str = ":memory:",
) -> Any:
    """Return (building if needed) the compiled research orchestrator."""
    if _NAME not in _COMPILED_GRAPHS:
        definition = _make_definition()
        factory = SubgraphFactory()
        _COMPILED_GRAPHS[_NAME] = await factory.build(
            definition, checkpointer_path=checkpointer_path
        )
    return _COMPILED_GRAPHS[_NAME]


__all__ = [
    "RESEARCH_AGENTS",
    "_make_definition",
    "_research_router",
    "get_compiled_research_orchestrator",
    "register_research_orchestrator",
]
