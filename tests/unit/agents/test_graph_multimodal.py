"""Graph-level wiring of the multimodal pipeline (Fase F, P3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from prismal.agents import graph as graph_module
from prismal.agents.graph import build_supervisor_graph


class _Settings:
    """Minimal settings stub toggling only the flags graph.py reads."""

    enable_subgraphs = False
    multimodal_enabled = True
    hierarchical_mode = False

    def __getattr__(self, _name: str) -> object:  # pragma: no cover - defensive
        return False


class TestBuildMultimodalNodes:
    async def test_returns_compiled_pipeline(self) -> None:
        nodes = await graph_module._build_multimodal_nodes()
        assert "multimodal_pipeline" in nodes
        assert nodes["multimodal_pipeline"] is not None


class TestGraphWiring:
    def test_node_added_when_multimodal_enabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(graph_module, "get_settings", lambda: _Settings())
        # A trivial stand-in node (build_supervisor_graph only registers it).
        async def _fake_pipeline(state: dict) -> dict:
            return {}

        compiled = build_supervisor_graph(
            checkpoint_path=tmp_path / "cp.db",
            multimodal_nodes={"multimodal_pipeline": _fake_pipeline},
        )
        assert "multimodal_pipeline" in compiled.get_graph().nodes

    def test_node_absent_when_multimodal_disabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Off(_Settings):
            multimodal_enabled = False

        monkeypatch.setattr(graph_module, "get_settings", lambda: _Off())

        async def _fake_pipeline(state: dict) -> dict:
            return {}

        compiled = build_supervisor_graph(
            checkpoint_path=tmp_path / "cp2.db",
            multimodal_nodes={"multimodal_pipeline": _fake_pipeline},
        )
        # Zero regression: the node is not wired when the flag is off.
        assert "multimodal_pipeline" not in compiled.get_graph().nodes
