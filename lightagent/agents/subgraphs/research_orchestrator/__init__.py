"""Research domain orchestrator subgraph (Phase 40 / SPEC-042).

Exposes the builder + accessor that instantiate the ``research``
sub-orchestrator — a compiled ``CompiledStateGraph`` that routes
research-flavoured requests between a small, focused set of leaf
agents (``researcher``, ``rag_agent``, and the future ``cua_agent``).
The entry node is a domain supervisor produced by
:func:`lightagent.agents.domain_supervisor.make_domain_supervisor`.
"""

from __future__ import annotations

from lightagent.agents.subgraphs.research_orchestrator.builder import (
    get_compiled_research_orchestrator,
    register_research_orchestrator,
)

__all__ = [
    "get_compiled_research_orchestrator",
    "register_research_orchestrator",
]
