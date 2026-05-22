"""Analysis domain orchestrator subgraph (Phase 40 / SPEC-042)."""

from __future__ import annotations

from prismal.agents.subgraphs.analysis_orchestrator.builder import (
    get_compiled_analysis_orchestrator,
    register_analysis_orchestrator,
)

__all__ = [
    "get_compiled_analysis_orchestrator",
    "register_analysis_orchestrator",
]
