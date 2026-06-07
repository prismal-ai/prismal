"""Graph-level wiring of the skynet swarm node (Fase S, S5-02)."""

from __future__ import annotations

from pathlib import Path

import pytest

from prismal.agents import graph as graph_module
from prismal.agents.graph import build_supervisor_graph


class _Settings:
    """Minimal settings stub toggling only the flags graph.py reads."""

    enable_subgraphs = False
    multimodal_enabled = False
    kokoro_enabled = False
    skynet_enabled = True
    hierarchical_mode = False

    def __getattr__(self, _name: str) -> object:  # pragma: no cover - defensive
        return False


async def _fake_skynet(state: dict) -> dict:
    return {}


class TestBuildSkynetNodes:
    async def test_returns_compiled_pipeline(self) -> None:
        nodes = await graph_module._build_skynet_nodes()
        assert "skynet" in nodes
        assert nodes["skynet"] is not None


class TestGraphWiring:
    def test_node_added_when_skynet_enabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(graph_module, "get_settings", lambda: _Settings())

        compiled = build_supervisor_graph(
            checkpoint_path=tmp_path / "cp.db",
            skynet_nodes={"skynet": _fake_skynet},
        )
        assert "skynet" in compiled.get_graph().nodes

    def test_node_absent_when_skynet_disabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Off(_Settings):
            skynet_enabled = False

        monkeypatch.setattr(graph_module, "get_settings", lambda: _Off())

        compiled = build_supervisor_graph(
            checkpoint_path=tmp_path / "cp2.db",
            skynet_nodes={"skynet": _fake_skynet},
        )
        # Zero regression: the node is not wired when the flag is off.
        assert "skynet" not in compiled.get_graph().nodes

    def test_graph_snapshot_unchanged_when_skynet_off(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With skynet_enabled=False the compiled topology is identical."""

        class _Off(_Settings):
            skynet_enabled = False

        monkeypatch.setattr(graph_module, "get_settings", lambda: _Off())

        baseline = build_supervisor_graph(checkpoint_path=tmp_path / "a.db")
        with_skynet_arg = build_supervisor_graph(
            checkpoint_path=tmp_path / "b.db",
            skynet_nodes={"skynet": _fake_skynet},
        )

        baseline_graph = baseline.get_graph()
        candidate_graph = with_skynet_arg.get_graph()
        assert set(baseline_graph.nodes) == set(candidate_graph.nodes)
        assert {(e.source, e.target) for e in baseline_graph.edges} == {
            (e.source, e.target) for e in candidate_graph.edges
        }
