"""Builder for the Software Development Pipeline subgraph.

Assembles the 6-agent dev pipeline (PO -> Architect -> Developer -> UnitTest ->
QA -> Reviewer) with approval gates and registers it with the
:class:`~lightagent.agents.subgraphs.registry.SubgraphRegistry`.

Usage::

    from lightagent.agents.subgraphs.dev_pipeline.builder import register_dev_pipeline

    await register_dev_pipeline()
"""

from __future__ import annotations

import structlog

from lightagent.agents.subgraphs.dev_pipeline.architect_agent import (
    architect_agent_node,
)
from lightagent.agents.subgraphs.dev_pipeline.developer_agent import (
    developer_agent_node,
)
from lightagent.agents.subgraphs.dev_pipeline.parallel_unit_test import (
    dev_test_aggregator_node,
    dev_unit_tester_node,
    module_dispatcher,
    module_router_node,
)
from lightagent.agents.subgraphs.dev_pipeline.po_agent import po_agent_node
from lightagent.agents.subgraphs.dev_pipeline.qa_agent import qa_agent_node
from lightagent.agents.subgraphs.dev_pipeline.reviewer_agent import (
    reviewer_agent_node,
)
from lightagent.agents.subgraphs.dev_pipeline.unit_test_agent import (
    unit_test_agent_node,
)
from lightagent.agents.subgraphs.factory import SubgraphFactory
from lightagent.agents.subgraphs.gates import failure_gate, score_gate
from lightagent.agents.subgraphs.registry import SubgraphDefinition, SubgraphRegistry

logger = structlog.get_logger("lightagent.subgraphs.dev_pipeline.builder")

_NAME = "dev_pipeline"
_DESCRIPTION = (
    "Software Development Pipeline: "
    "PO -> Architect -> Developer -> UnitTest -> QA -> Reviewer"
)

# Approval gate: reviewer score >= 0.8 -> END, else back to developer
_REVIEWER_GATE = score_gate(
    field="dev_pipeline.review_result.score",
    threshold=0.8,
    on_pass="__end__",  # noqa: S106
    on_fail="developer",
    max_iterations=3,
)

# Approval gate: failing tests -> back to developer
_TEST_GATE = failure_gate(
    field="dev_pipeline.test_report.failing_tests",
    on_pass="qa_agent",  # noqa: S106
    on_fail="developer",
    max_iterations=3,
)

# Module-level cache for compiled graphs (accessed by graph.py)
_COMPILED_GRAPHS: dict[str, object] = {}


def _make_definition() -> SubgraphDefinition:
    """Build the SubgraphDefinition for the dev_pipeline.

    Returns:
        A fully configured :class:`SubgraphDefinition`.
    """
    return SubgraphDefinition(
        name=_NAME,
        description=_DESCRIPTION,
        entry_point="po_agent",
        nodes={
            "po_agent": po_agent_node,
            "architect": architect_agent_node,
            "developer": developer_agent_node,
            # Phase 34 (T-313): module router + parallel unit testing path.
            "module_router": module_router_node,
            "dev_unit_tester": dev_unit_tester_node,
            "dev_test_aggregator": dev_test_aggregator_node,
            "unit_tester": unit_test_agent_node,
            "qa_agent": qa_agent_node,
            "reviewer": reviewer_agent_node,
        },
        edges=[
            ("po_agent", "architect"),
            ("architect", "developer"),
            # Developer routes through the module router so the dispatcher
            # decides between the parallel and sequential paths.
            ("developer", "module_router"),
            # Each parallel worker converges on the aggregator.
            ("dev_unit_tester", "dev_test_aggregator"),
            ("qa_agent", "reviewer"),
        ],
        conditional_edges={
            # Both single-module (unit_tester) and merged multi-module
            # (dev_test_aggregator) paths feed the same gate.
            "unit_tester": _TEST_GATE,
            "dev_test_aggregator": _TEST_GATE,
            "reviewer": _REVIEWER_GATE,
        },
        # Phase 34: dispatcher fans out to ``dev_unit_tester`` when modules
        # are present, otherwise routes to the sequential ``unit_tester``.
        send_edges=[("module_router", module_dispatcher)],
    )


async def register_dev_pipeline(
    checkpointer_path: str = "data/db/checkpoints_subgraph_dev_pipeline.db",
) -> None:
    """Build and register the dev_pipeline subgraph.

    Idempotent -- skips registration if already registered.

    Args:
        checkpointer_path: SQLite file path for checkpointing.  Use
            ``":memory:"`` in tests.
    """
    registry = SubgraphRegistry.get_instance()
    if registry.get(_NAME) is not None:
        logger.info("dev_pipeline.already_registered")
        return

    definition = _make_definition()
    factory = SubgraphFactory()
    compiled = await factory.build(definition, checkpointer_path=checkpointer_path)
    await registry.register(_NAME, definition)

    _COMPILED_GRAPHS[_NAME] = compiled
    logger.info("dev_pipeline.registered")


async def get_compiled_dev_pipeline(checkpointer_path: str = ":memory:") -> object:
    """Return the compiled dev_pipeline graph (building it if needed).

    Args:
        checkpointer_path: SQLite path used only on first build.

    Returns:
        Compiled ``CompiledStateGraph``.
    """
    if _NAME not in _COMPILED_GRAPHS:
        definition = _make_definition()
        factory = SubgraphFactory()
        _COMPILED_GRAPHS[_NAME] = await factory.build(
            definition, checkpointer_path=checkpointer_path
        )
    return _COMPILED_GRAPHS[_NAME]


__all__ = ["get_compiled_dev_pipeline", "register_dev_pipeline"]
