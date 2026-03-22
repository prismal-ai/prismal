"""
Builder for the Financial Analyst subgraph.

Assembles the 5-agent financial pipeline:
  market_data_collector -> technical_analyst -> fundamental_analyst
  -> risk_sentiment_analyst -> report_generator

No approval gates are needed (all analysis is sequential and read-only).
The pipeline uses an isolated checkpointer at
``data/db/checkpoints_subgraph_financial_analyst.db``.

Usage::

    from lightagent.agents.subgraphs.financial.builder import (
        register_financial_analyst,
    )

    await register_financial_analyst()
"""

from __future__ import annotations

import structlog

from lightagent.agents.subgraphs.factory import SubgraphFactory
from lightagent.agents.subgraphs.financial.fundamental_analyst import (
    fundamental_analyst_node,
)
from lightagent.agents.subgraphs.financial.market_data_collector import (
    market_data_collector_node,
)
from lightagent.agents.subgraphs.financial.report_generator import (
    report_generator_node,
)
from lightagent.agents.subgraphs.financial.risk_sentiment_analyst import (
    risk_sentiment_analyst_node,
)
from lightagent.agents.subgraphs.financial.technical_analyst import (
    technical_analyst_node,
)
from lightagent.agents.subgraphs.registry import SubgraphDefinition, SubgraphRegistry

logger = structlog.get_logger("lightagent.subgraphs.financial.builder")

_NAME = "financial_analyst"
_DESCRIPTION = (
    "Financial Analysis Pipeline: "
    "Market Data Collector -> Technical Analyst -> Fundamental Analyst "
    "-> Risk/Sentiment Analyst -> Report Generator"
)

# Module-level cache for compiled graphs (accessed by graph.py via
# get_compiled_financial_analyst)
_COMPILED_GRAPHS: dict[str, object] = {}


def _make_definition() -> SubgraphDefinition:
    """
    Build the SubgraphDefinition for the financial_analyst pipeline.

    Returns:
        A fully configured :class:`SubgraphDefinition`.
    """
    return SubgraphDefinition(
        name=_NAME,
        description=_DESCRIPTION,
        entry_point="market_data_collector",
        nodes={
            "market_data_collector": market_data_collector_node,
            "technical_analyst": technical_analyst_node,
            "fundamental_analyst": fundamental_analyst_node,
            "risk_sentiment_analyst": risk_sentiment_analyst_node,
            "report_generator": report_generator_node,
        },
        edges=[
            ("market_data_collector", "technical_analyst"),
            ("technical_analyst", "fundamental_analyst"),
            ("fundamental_analyst", "risk_sentiment_analyst"),
            ("risk_sentiment_analyst", "report_generator"),
        ],
        conditional_edges={},
    )


async def register_financial_analyst(
    checkpointer_path: str = "data/db/checkpoints_subgraph_financial_analyst.db",
) -> None:
    """
    Build and register the financial_analyst subgraph.

    Idempotent -- skips registration if already registered.

    Args:
        checkpointer_path: SQLite file path for checkpointing.
            Use ``":memory:"`` in tests.
    """
    registry = SubgraphRegistry.get_instance()
    if registry.get(_NAME) is not None:
        logger.info("financial_analyst.already_registered")
        return

    definition = _make_definition()
    factory = SubgraphFactory()
    compiled = await factory.build(definition, checkpointer_path=checkpointer_path)
    await registry.register(_NAME, definition)

    _COMPILED_GRAPHS[_NAME] = compiled
    logger.info("financial_analyst.registered")


async def get_compiled_financial_analyst(
    checkpointer_path: str = ":memory:",
) -> object:
    """
    Return the compiled financial_analyst graph (building it if needed).

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


__all__ = ["get_compiled_financial_analyst", "register_financial_analyst"]
