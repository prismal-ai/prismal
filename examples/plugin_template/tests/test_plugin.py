"""Smoke tests for the example plugin template."""

from __future__ import annotations

from prismal_x_example.nodes import example_classifier
from prismal_x_example.plugin import register_example_pipeline

from prismal.agents.subgraphs.registry import SubgraphRegistry


def test_register_example_pipeline() -> None:
    registry = SubgraphRegistry()
    register_example_pipeline(registry)
    assert registry.get("example_pipeline") is not None


def test_example_classifier_is_prismal_node() -> None:
    assert hasattr(example_classifier, "__prismal_node__")
    assert example_classifier.__prismal_node__.name == "example_classifier"
