"""Builder for the ``engineering_orchestrator`` domain subgraph.

SPEC-042 AC-042-1 — the engineering domain bundles every leaf agent
that **writes, plans, or manipulates code and files**: ``coder``
(ReAct tool-calling), ``codeact`` (direct Python generation),
``planner`` (SDD spec decomposition), ``file_manager``, and
``skill_manager``. The root supervisor delegates any
"build / implement / refactor / plan" request to this subgraph when
``PRISMAL_HIERARCHICAL_MODE=true``.

Topology mirrors the research orchestrator (domain supervisor → 5
leaves → back to supervisor) — see
:mod:`prismal.agents.subgraphs.research_orchestrator.builder` for
the design rationale.
"""

from __future__ import annotations

from typing import Any

import structlog

from prismal.agents.codeact_agent import codeact_node
from prismal.agents.coder import coder_node
from prismal.agents.domain_supervisor import make_domain_supervisor
from prismal.agents.file_manager import file_manager_node
from prismal.agents.planner import planner_node
from prismal.agents.skill_manager import skill_manager_node
from prismal.agents.subgraphs.factory import SubgraphFactory
from prismal.agents.subgraphs.registry import (
    SubgraphDefinition,
    SubgraphRegistry,
)

logger = structlog.get_logger("prismal.subgraphs.engineering_orchestrator.builder")

_NAME = "engineering_orchestrator"
_DESCRIPTION = (
    "Engineering domain orchestrator — routes code writing, planning, "
    "filesystem, and skill-management requests between coder, codeact, "
    "planner, file_manager and skill_manager."
)
_SUPERVISOR_DESCRIPTION = (
    "Handle requests that require writing, modifying, planning or "
    "executing code and files. Typical tasks: implement a feature, "
    "refactor a module, draft a PRD or technical spec, create / read "
    "/ move files under the workspace, install or activate a skill."
)

#: Leaf agents this domain may route to. ``coder`` handles small / "
#: "surgical edits while ``codeact`` owns multi-step iterative coding;
#: the domain supervisor is prompted to pick between them based on
#: task complexity (same heuristics as the root supervisor).
ENGINEERING_AGENTS: tuple[str, ...] = (
    "coder",
    "codeact",
    "planner",
    "file_manager",
    "skill_manager",
)

_COMPILED_GRAPHS: dict[str, object] = {}


def _engineering_router(state: dict[str, Any]) -> str:
    """Conditional-edge router for ``engineering_supervisor``.

    Identical shape to the research router — defers to
    ``state["next_agent"]`` as written by the domain supervisor and
    rejects anything outside :data:`ENGINEERING_AGENTS`.
    """
    next_agent = state.get("next_agent")
    if next_agent is None or next_agent == "END":
        return "__end__"
    if next_agent in ENGINEERING_AGENTS:
        return str(next_agent)
    logger.warning(
        "engineering_orchestrator.router_unknown_next_agent",
        next_agent=next_agent,
    )
    return "__end__"


def _make_definition() -> SubgraphDefinition:
    """Build the :class:`SubgraphDefinition` for the engineering domain."""
    supervisor_node = make_domain_supervisor(
        domain_name="engineering",
        agents=list(ENGINEERING_AGENTS),
        domain_description=_SUPERVISOR_DESCRIPTION,
    )
    return SubgraphDefinition(
        name=_NAME,
        description=_DESCRIPTION,
        entry_point="engineering_supervisor",
        nodes={
            "engineering_supervisor": supervisor_node,
            "coder": coder_node,
            "codeact": codeact_node,
            "planner": planner_node,
            "file_manager": file_manager_node,
            "skill_manager": skill_manager_node,
        },
        edges=[
            ("coder", "engineering_supervisor"),
            ("codeact", "engineering_supervisor"),
            ("planner", "engineering_supervisor"),
            ("file_manager", "engineering_supervisor"),
            ("skill_manager", "engineering_supervisor"),
        ],
        conditional_edges={
            "engineering_supervisor": _engineering_router,
        },
    )


async def register_engineering_orchestrator(
    checkpointer_path: str = ("data/db/checkpoints_subgraph_engineering_orchestrator.db"),
) -> None:
    """Compile and register the engineering orchestrator (idempotent)."""
    registry = SubgraphRegistry.get_instance()
    if registry.get(_NAME) is not None:
        logger.info("engineering_orchestrator.already_registered")
        return

    definition = _make_definition()
    factory = SubgraphFactory()
    compiled = await factory.build(definition, checkpointer_path=checkpointer_path)
    await registry.register(_NAME, definition)

    _COMPILED_GRAPHS[_NAME] = compiled
    logger.info("engineering_orchestrator.registered")


async def get_compiled_engineering_orchestrator(
    checkpointer_path: str = ":memory:",
) -> Any:
    """Return (building if needed) the compiled engineering orchestrator."""
    if _NAME not in _COMPILED_GRAPHS:
        definition = _make_definition()
        factory = SubgraphFactory()
        _COMPILED_GRAPHS[_NAME] = await factory.build(
            definition, checkpointer_path=checkpointer_path
        )
    return _COMPILED_GRAPHS[_NAME]


__all__ = [
    "ENGINEERING_AGENTS",
    "_engineering_router",
    "_make_definition",
    "get_compiled_engineering_orchestrator",
    "register_engineering_orchestrator",
]
