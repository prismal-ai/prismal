"""Builder for the data_etl subgraph (C3).

Pipeline shape::

    extractor → validator → transformer → loader → auditor
                       └─(validation fails)──────────↑

The ``validator`` has a conditional edge driven by
``make_etl_branching_gate`` that short-circuits to ``auditor`` when the
DataFrame fails validation — the transform + load stages are skipped and
the audit reports the failure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lightagent.agents.subgraphs.data_etl.auditor_node import make_auditor_node
from lightagent.agents.subgraphs.data_etl.extractor_node import make_extractor_node
from lightagent.agents.subgraphs.data_etl.loader_node import make_loader_node
from lightagent.agents.subgraphs.data_etl.transformer_node import make_transformer_node
from lightagent.agents.subgraphs.data_etl.validator_node import (
    make_etl_branching_gate,
    make_validator_node,
)
from lightagent.agents.subgraphs.registry import SubgraphDefinition
from lightagent.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import polars as pl

    from lightagent.core.config import Settings

logger = get_logger("lightagent.subgraphs.data_etl.builder")

_NAME = "data_etl"
_DESCRIPTION = "Data ETL pipeline: extractor → validator → transformer → loader → auditor"


def build_data_etl_subgraph(
    extractor_fn: (Callable[[dict[str, Any]], Awaitable[pl.DataFrame]] | None) = None,
    validator_fn: (Callable[[pl.DataFrame, dict[str, Any]], tuple[bool, list[str]]] | None) = None,
    transformer_fn: (
        Callable[
            [pl.DataFrame, list[dict[str, Any]]],
            Awaitable[tuple[pl.DataFrame, list[str]]] | tuple[pl.DataFrame, list[str]],
        ]
        | None
    ) = None,
    loader_fn: (Callable[[pl.DataFrame, dict[str, Any]], Awaitable[int] | int] | None) = None,
    required_columns: list[str] | None = None,
    settings: Settings | None = None,
) -> SubgraphDefinition:
    """Build the data_etl :class:`SubgraphDefinition`.

    Args:
        extractor_fn: Override for the default polars-based extractor.
        validator_fn: Override for the default shape/schema validator.
        transformer_fn: Override for the default select/filter/rename
            transformer.
        loader_fn: Override for the default polars-based CSV/Parquet loader.
        required_columns: Optional list used by the default validator to
            enforce column presence.
        settings: Reserved for future auto-wiring.

    Returns:
        :class:`SubgraphDefinition` with 5 nodes and a conditional edge on
        the validator.
    """
    del settings  # reserved

    extractor = make_extractor_node(extractor_fn=extractor_fn)
    validator = make_validator_node(required_columns=required_columns, validator_fn=validator_fn)
    branching_gate = make_etl_branching_gate()
    transformer = make_transformer_node(transformer_fn=transformer_fn)
    loader = make_loader_node(loader_fn=loader_fn)
    auditor = make_auditor_node()

    definition = SubgraphDefinition(
        name=_NAME,
        description=_DESCRIPTION,
        entry_point="extractor",
        nodes={
            "extractor": extractor,
            "validator": validator,
            "transformer": transformer,
            "loader": loader,
            "auditor": auditor,
        },
        edges=[
            ("extractor", "validator"),
            ("transformer", "loader"),
            ("loader", "auditor"),
        ],
        conditional_edges={
            "validator": branching_gate,
        },
    )
    logger.info("data_etl_subgraph_built", nodes=list(definition.nodes.keys()))
    return definition


async def register_data_etl(
    settings: Settings | None = None,
) -> None:
    """Register the data_etl subgraph (idempotent). Uses default polars IO."""
    from lightagent.agents.subgraphs.registry import SubgraphRegistry

    registry = SubgraphRegistry.get_instance()
    if registry.get(_NAME) is not None:
        logger.info("data_etl.already_registered")
        return
    definition = build_data_etl_subgraph(settings=settings)
    await registry.register(_NAME, definition)
    logger.info("data_etl.registered")


__all__ = ["build_data_etl_subgraph", "register_data_etl"]
