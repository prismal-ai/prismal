"""Multimodal pipeline subgraph (Fase F, SPEC-MM-SUB-001).

Exports ``build_multimodal_subgraph()`` (returns a ``SubgraphDefinition``) and
the idempotent ``register_multimodal_pipeline()``, mirroring the existing
``register_ml_pipeline`` pattern.
"""

from __future__ import annotations

from prismal.agents.subgraphs.multimodal_pipeline.builder import (
    build_multimodal_subgraph,
    register_multimodal_pipeline,
)

__all__ = [
    "build_multimodal_subgraph",
    "register_multimodal_pipeline",
]
