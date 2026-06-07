"""Graph-level wiring of the kokoro deliberation node (Fase K, K7-02 / K8-06)."""

from __future__ import annotations

from pathlib import Path

import pytest

from prismal.agents import graph as graph_module
from prismal.agents.graph import build_supervisor_graph


class _Settings:
    """Minimal settings stub toggling only the flags graph.py reads."""

    enable_subgraphs = False
    multimodal_enabled = False
    kokoro_enabled = True
    hierarchical_mode = False

    def __getattr__(self, _name: str) -> object:  # pragma: no cover - defensive
        return False


async def _fake_kokoro(state: dict) -> dict:
    return {}


class TestBuildKokoroNodes:
    async def test_returns_compiled_pipeline(self) -> None:
        nodes = await graph_module._build_kokoro_nodes()
        assert "kokoro" in nodes
        assert nodes["kokoro"] is not None


class TestGraphWiring:
    def test_node_added_when_kokoro_enabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(graph_module, "get_settings", lambda: _Settings())

        compiled = build_supervisor_graph(
            checkpoint_path=tmp_path / "cp.db",
            kokoro_nodes={"kokoro": _fake_kokoro},
        )
        assert "kokoro" in compiled.get_graph().nodes

    def test_node_absent_when_kokoro_disabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Off(_Settings):
            kokoro_enabled = False

        monkeypatch.setattr(graph_module, "get_settings", lambda: _Off())

        compiled = build_supervisor_graph(
            checkpoint_path=tmp_path / "cp2.db",
            kokoro_nodes={"kokoro": _fake_kokoro},
        )
        # Zero regression: the node is not wired when the flag is off.
        assert "kokoro" not in compiled.get_graph().nodes

    def test_graph_snapshot_unchanged_when_kokoro_off(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """K8-06: with kokoro_enabled=False the compiled topology is identical."""

        class _Off(_Settings):
            kokoro_enabled = False

        monkeypatch.setattr(graph_module, "get_settings", lambda: _Off())

        baseline = build_supervisor_graph(checkpoint_path=tmp_path / "a.db")
        with_kokoro_arg = build_supervisor_graph(
            checkpoint_path=tmp_path / "b.db",
            kokoro_nodes={"kokoro": _fake_kokoro},
        )

        baseline_graph = baseline.get_graph()
        candidate_graph = with_kokoro_arg.get_graph()
        assert set(baseline_graph.nodes) == set(candidate_graph.nodes)
        assert {(e.source, e.target) for e in baseline_graph.edges} == {
            (e.source, e.target) for e in candidate_graph.edges
        }
