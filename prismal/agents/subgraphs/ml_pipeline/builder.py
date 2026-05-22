"""
Builder for the ML/DL Pipeline subgraph.

Assembles the 6-agent ML pipeline:
data_ingester -> eda_analyst -> feature_engineer -> model_trainer
-> model_evaluator -> model_exporter

Includes a model quality gate on ``model_evaluator``:
- primary_score >= 0.7 -> model_exporter
- primary_score < 0.7 -> model_trainer (retrain, max 3 iterations)

Usage::

    from prismal.agents.subgraphs.ml_pipeline.builder import register_ml_pipeline

    await register_ml_pipeline()
"""

from __future__ import annotations

import structlog

from prismal.agents.subgraphs.factory import SubgraphFactory
from prismal.agents.subgraphs.gates import score_gate
from prismal.agents.subgraphs.ml_pipeline.data_ingester import data_ingester_node
from prismal.agents.subgraphs.ml_pipeline.eda_analyst import eda_analyst_node
from prismal.agents.subgraphs.ml_pipeline.feature_engineer import (
    feature_engineer_node,
)
from prismal.agents.subgraphs.ml_pipeline.model_evaluator import model_evaluator_node
from prismal.agents.subgraphs.ml_pipeline.model_exporter import model_exporter_node
from prismal.agents.subgraphs.ml_pipeline.model_trainer import model_trainer_node
from prismal.agents.subgraphs.registry import SubgraphDefinition, SubgraphRegistry

logger = structlog.get_logger("lightagent.subgraphs.ml_pipeline.builder")

_NAME = "ml_pipeline"
_DESCRIPTION = (
    "ML/DL Agent Pipeline: "
    "Data Ingester -> EDA Analyst -> Feature Engineer -> "
    "Model Trainer -> Model Evaluator -> Model Exporter"
)

# Model quality gate: primary_score >= 0.7 -> model_exporter, else -> model_trainer.
# max_iterations=3 prevents infinite retrain loops (CLAUDE.md requirement).
_MODEL_QUALITY_GATE = score_gate(
    field="ml_pipeline.evaluation_report.primary_score",
    threshold=0.7,
    on_pass="model_exporter",  # noqa: S106
    on_fail="model_trainer",
    max_iterations=3,
)

# Module-level cache for compiled graphs (accessed by graph.py via
# get_compiled_ml_pipeline)
_COMPILED_GRAPHS: dict[str, object] = {}


def _make_definition() -> SubgraphDefinition:
    """
    Build the SubgraphDefinition for the ml_pipeline.

    Returns:
        A fully configured :class:`SubgraphDefinition`.
    """
    return SubgraphDefinition(
        name=_NAME,
        description=_DESCRIPTION,
        entry_point="data_ingester",
        nodes={
            "data_ingester": data_ingester_node,
            "eda_analyst": eda_analyst_node,
            "feature_engineer": feature_engineer_node,
            "model_trainer": model_trainer_node,
            "model_evaluator": model_evaluator_node,
            "model_exporter": model_exporter_node,
        },
        edges=[
            ("data_ingester", "eda_analyst"),
            ("eda_analyst", "feature_engineer"),
            ("feature_engineer", "model_trainer"),
            ("model_trainer", "model_evaluator"),
        ],
        conditional_edges={
            "model_evaluator": _MODEL_QUALITY_GATE,
        },
    )


async def register_ml_pipeline(
    checkpointer_path: str = "data/db/checkpoints_subgraph_ml_pipeline.db",
) -> None:
    """
    Build and register the ml_pipeline subgraph.

    Idempotent -- skips registration if already registered.

    Args:
        checkpointer_path: SQLite file path for checkpointing.  Use
            ``":memory:"`` in tests.
    """
    registry = SubgraphRegistry.get_instance()
    if registry.get(_NAME) is not None:
        logger.info("ml_pipeline.already_registered")
        return

    definition = _make_definition()
    factory = SubgraphFactory()
    compiled = await factory.build(definition, checkpointer_path=checkpointer_path)
    await registry.register(_NAME, definition)

    _COMPILED_GRAPHS[_NAME] = compiled
    logger.info("ml_pipeline.registered")


async def get_compiled_ml_pipeline(checkpointer_path: str = ":memory:") -> object:
    """
    Return the compiled ml_pipeline graph (building it if needed).

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


__all__ = ["get_compiled_ml_pipeline", "register_ml_pipeline"]
