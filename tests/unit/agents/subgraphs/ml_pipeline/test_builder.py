"""Unit tests for ml_pipeline subgraph builder."""

import pytest


@pytest.mark.asyncio
async def test_register_ml_pipeline_idempotent() -> None:
    """register_ml_pipeline() can be called twice without error."""
    from prismal.agents.subgraphs.registry import SubgraphRegistry

    # Reset singleton for test isolation
    SubgraphRegistry._instance = None

    from prismal.agents.subgraphs.ml_pipeline.builder import register_ml_pipeline

    await register_ml_pipeline(checkpointer_path=":memory:")
    # Second call should be a no-op (already registered)
    await register_ml_pipeline(checkpointer_path=":memory:")

    registry = SubgraphRegistry.get_instance()
    assert "ml_pipeline" in registry.list()


@pytest.mark.asyncio
async def test_get_compiled_ml_pipeline_returns_graph() -> None:
    """get_compiled_ml_pipeline() returns a compiled graph object."""
    from prismal.agents.subgraphs.ml_pipeline.builder import (
        _COMPILED_GRAPHS,
        get_compiled_ml_pipeline,
    )

    _COMPILED_GRAPHS.clear()

    graph = await get_compiled_ml_pipeline(checkpointer_path=":memory:")
    assert graph is not None


def test_ml_pipeline_definition_has_six_nodes() -> None:
    """ML pipeline definition has exactly 6 agent nodes."""
    from prismal.agents.subgraphs.ml_pipeline.builder import _make_definition

    defn = _make_definition()
    assert len(defn.nodes) == 6
    assert "data_ingester" in defn.nodes
    assert "eda_analyst" in defn.nodes
    assert "feature_engineer" in defn.nodes
    assert "model_trainer" in defn.nodes
    assert "model_evaluator" in defn.nodes
    assert "model_exporter" in defn.nodes


def test_ml_pipeline_definition_entry_point() -> None:
    """ML pipeline entry point is data_ingester."""
    from prismal.agents.subgraphs.ml_pipeline.builder import _make_definition

    defn = _make_definition()
    assert defn.entry_point == "data_ingester"


def test_ml_pipeline_has_quality_gate() -> None:
    """ML pipeline has a conditional edge on model_evaluator."""
    from prismal.agents.subgraphs.ml_pipeline.builder import _make_definition

    defn = _make_definition()
    assert "model_evaluator" in defn.conditional_edges


def test_ml_pipeline_in_supervisor_members() -> None:
    """ml_pipeline is listed in supervisor MEMBERS."""
    from prismal.agents.supervisor import MEMBERS

    assert "ml_pipeline" in MEMBERS
