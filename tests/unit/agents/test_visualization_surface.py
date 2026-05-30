"""Tests for the public viz surface: prismal.langgraph re-export + supervisor helper (V3)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


class TestLanggraphReexport:
    def test_viz_symbols_reexported(self) -> None:
        import prismal.langgraph as lg
        from prismal.agents import visualization as viz

        assert lg.to_mermaid is viz.to_mermaid
        assert lg.to_mermaid_png is viz.to_mermaid_png
        assert lg.visualize is viz.visualize
        assert lg.save_graph_image is viz.save_graph_image

    def test_symbols_in_all(self) -> None:
        import prismal.langgraph as lg

        for name in ("to_mermaid", "to_mermaid_png", "visualize", "save_graph_image"):
            assert name in lg.__all__


class TestVisualizeSupervisorGraph:
    def test_delegates_with_passed_graph(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from prismal.agents import graph as graph_module

        captured: dict[str, object] = {}
        monkeypatch.setattr(
            "prismal.agents.visualization.visualize",
            lambda obj: captured.setdefault("obj", obj),
        )
        sentinel = SimpleNamespace(get_graph=lambda: None)
        graph_module.visualize_supervisor_graph(sentinel)
        assert captured["obj"] is sentinel

    def test_defaults_to_compiled_graph(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from prismal.agents import graph as graph_module

        sentinel = SimpleNamespace(get_graph=lambda: None)
        monkeypatch.setattr(graph_module, "get_compiled_graph", lambda: sentinel)
        captured: dict[str, object] = {}
        monkeypatch.setattr(
            "prismal.agents.visualization.visualize",
            lambda obj: captured.setdefault("obj", obj),
        )
        graph_module.visualize_supervisor_graph()
        assert captured["obj"] is sentinel
