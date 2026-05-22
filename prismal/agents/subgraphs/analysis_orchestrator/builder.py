"""Builder for the ``analysis_orchestrator`` domain subgraph.

SPEC-042 AC-042-1 — the analysis domain aggregates every leaf that
**produces structured analytical output**: ``data_analyst`` (SQL /
DataFrame / charts), plus the three pre-existing pipeline subgraphs
``dev_pipeline`` (software development pipeline), ``ml_pipeline``
(AutoML / training / evaluation), and ``financial_analyst`` (market
data → technical → fundamental → risk → report). The root supervisor
routes analytical tasks to this orchestrator when
``PRISMAL_HIERARCHICAL_MODE=true``.

Because three of the four leaves are themselves compiled
``CompiledStateGraph`` instances built by their own async factories,
this builder is an ``async`` function that awaits those factories
before calling :meth:`SubgraphFactory.build`. The compiled pipeline
graphs can also be passed in explicitly — tests lean on that to wire
lightweight stub nodes instead of the real pipelines.
"""

from __future__ import annotations

from typing import Any

import structlog

from prismal.agents.data_analyst import data_analyst_node
from prismal.agents.domain_supervisor import make_domain_supervisor
from prismal.agents.subgraphs.factory import SubgraphFactory
from prismal.agents.subgraphs.registry import (
    SubgraphDefinition,
    SubgraphRegistry,
)

logger = structlog.get_logger("prismal.subgraphs.analysis_orchestrator.builder")

_NAME = "analysis_orchestrator"
_DESCRIPTION = (
    "Analysis domain orchestrator — routes SQL / DataFrame analytics, "
    "ML training, software-development pipelines and financial "
    "analysis between data_analyst, dev_pipeline, ml_pipeline, and "
    "financial_analyst."
)
_SUPERVISOR_DESCRIPTION = (
    "Handle requests that require producing structured analytical "
    "output. Typical tasks: run SQL / DataFrame queries and make "
    "charts (data_analyst); train, evaluate or deploy an ML model "
    "(ml_pipeline); execute a full PO → architect → developer → QA "
    "software pipeline (dev_pipeline); analyse a stock, crypto or "
    "forex asset with technical + fundamental + risk reports "
    "(financial_analyst)."
)

#: Leaf names this domain may route to. The pipeline entries here are
#: node names — the actual compiled graphs are plugged in at build
#: time so the domain supervisor only ever reasons in terms of these
#: four stable identifiers.
ANALYSIS_AGENTS: tuple[str, ...] = (
    "data_analyst",
    "dev_pipeline",
    "ml_pipeline",
    "financial_analyst",
)

_COMPILED_GRAPHS: dict[str, object] = {}


def _analysis_router(state: dict[str, Any]) -> str:
    """Conditional-edge router for ``analysis_supervisor``."""
    next_agent = state.get("next_agent")
    if next_agent is None or next_agent == "END":
        return "__end__"
    if next_agent in ANALYSIS_AGENTS:
        return str(next_agent)
    logger.warning(
        "analysis_orchestrator.router_unknown_next_agent",
        next_agent=next_agent,
    )
    return "__end__"


async def _make_definition(
    *,
    dev_pipeline_graph: Any = None,
    ml_pipeline_graph: Any = None,
    financial_analyst_graph: Any = None,
) -> SubgraphDefinition:
    """Build the :class:`SubgraphDefinition` for the analysis domain.

    Three of the four leaves are themselves compiled subgraphs. When
    the caller does not pass them explicitly we lazy-import and await
    their ``get_compiled_*`` helpers — each of those builders owns its
    own module-level cache, so repeated calls are cheap.

    Test suites that want to exercise the orchestrator wiring without
    paying for three real pipelines can pass lightweight stub nodes
    via the keyword arguments; any non-``None`` value is used as-is
    and bound to the corresponding node name.

    Args:
        dev_pipeline_graph: Pre-compiled dev_pipeline graph / stub.
        ml_pipeline_graph: Pre-compiled ml_pipeline graph / stub.
        financial_analyst_graph: Pre-compiled financial_analyst graph /
            stub.

    Returns:
        A fully configured :class:`SubgraphDefinition`.
    """
    if dev_pipeline_graph is None:
        from prismal.agents.subgraphs.dev_pipeline.builder import (
            get_compiled_dev_pipeline,
        )

        dev_pipeline_graph = await get_compiled_dev_pipeline()

    if ml_pipeline_graph is None:
        from prismal.agents.subgraphs.ml_pipeline.builder import (
            get_compiled_ml_pipeline,
        )

        ml_pipeline_graph = await get_compiled_ml_pipeline()

    if financial_analyst_graph is None:
        from prismal.agents.subgraphs.financial.builder import (
            get_compiled_financial_analyst,
        )

        financial_analyst_graph = await get_compiled_financial_analyst()

    supervisor_node = make_domain_supervisor(
        domain_name="analysis",
        agents=list(ANALYSIS_AGENTS),
        domain_description=_SUPERVISOR_DESCRIPTION,
    )
    return SubgraphDefinition(
        name=_NAME,
        description=_DESCRIPTION,
        entry_point="analysis_supervisor",
        nodes={
            "analysis_supervisor": supervisor_node,
            "data_analyst": data_analyst_node,
            # The three pipeline leaves are pre-compiled subgraphs —
            # LangGraph accepts a ``CompiledStateGraph`` anywhere a
            # node callable is expected and invokes it transparently.
            "dev_pipeline": dev_pipeline_graph,
            "ml_pipeline": ml_pipeline_graph,
            "financial_analyst": financial_analyst_graph,
        },
        edges=[
            ("data_analyst", "analysis_supervisor"),
            ("dev_pipeline", "analysis_supervisor"),
            ("ml_pipeline", "analysis_supervisor"),
            ("financial_analyst", "analysis_supervisor"),
        ],
        conditional_edges={
            "analysis_supervisor": _analysis_router,
        },
    )


async def register_analysis_orchestrator(
    checkpointer_path: str = ("data/db/checkpoints_subgraph_analysis_orchestrator.db"),
    *,
    dev_pipeline_graph: Any = None,
    ml_pipeline_graph: Any = None,
    financial_analyst_graph: Any = None,
) -> None:
    """Compile and register the analysis orchestrator (idempotent).

    Args:
        checkpointer_path: SQLite file path for the subgraph's
            isolated checkpointer. Use ``":memory:"`` in tests.
        dev_pipeline_graph: Optional pre-compiled dev_pipeline stub.
        ml_pipeline_graph: Optional pre-compiled ml_pipeline stub.
        financial_analyst_graph: Optional pre-compiled
            financial_analyst stub.
    """
    registry = SubgraphRegistry.get_instance()
    if registry.get(_NAME) is not None:
        logger.info("analysis_orchestrator.already_registered")
        return

    definition = await _make_definition(
        dev_pipeline_graph=dev_pipeline_graph,
        ml_pipeline_graph=ml_pipeline_graph,
        financial_analyst_graph=financial_analyst_graph,
    )
    factory = SubgraphFactory()
    compiled = await factory.build(definition, checkpointer_path=checkpointer_path)
    await registry.register(_NAME, definition)

    _COMPILED_GRAPHS[_NAME] = compiled
    logger.info("analysis_orchestrator.registered")


async def get_compiled_analysis_orchestrator(
    checkpointer_path: str = ":memory:",
    *,
    dev_pipeline_graph: Any = None,
    ml_pipeline_graph: Any = None,
    financial_analyst_graph: Any = None,
) -> Any:
    """Return (building if needed) the compiled analysis orchestrator."""
    if _NAME not in _COMPILED_GRAPHS:
        definition = await _make_definition(
            dev_pipeline_graph=dev_pipeline_graph,
            ml_pipeline_graph=ml_pipeline_graph,
            financial_analyst_graph=financial_analyst_graph,
        )
        factory = SubgraphFactory()
        _COMPILED_GRAPHS[_NAME] = await factory.build(
            definition, checkpointer_path=checkpointer_path
        )
    return _COMPILED_GRAPHS[_NAME]


__all__ = [
    "ANALYSIS_AGENTS",
    "_analysis_router",
    "_make_definition",
    "get_compiled_analysis_orchestrator",
    "register_analysis_orchestrator",
]
