"""Tests for the graph visualization helpers (V1)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from prismal.agents import visualization as viz
from prismal.agents.subgraphs.debate_consensus.builder import (
    build_debate_consensus_subgraph,
)
from prismal.agents.subgraphs.multimodal_pipeline import build_multimodal_subgraph
from prismal.agents.visualization import (
    save_graph_image,
    to_mermaid,
    to_mermaid_png,
    visualize,
)


class TestToMermaid:
    def test_subgraph_definition_renders_nodes(self) -> None:
        mermaid = to_mermaid(build_multimodal_subgraph())
        assert isinstance(mermaid, str)
        assert "router" in mermaid
        assert "fusion_node" in mermaid

    def test_another_subgraph_renders(self) -> None:
        mermaid = to_mermaid(build_debate_consensus_subgraph())
        assert "proponent" in mermaid

    def test_compiled_graph_renders(self) -> None:
        from prismal.agents.subgraphs.factory import assemble_state_graph

        compiled = assemble_state_graph(build_multimodal_subgraph()).compile()
        mermaid = to_mermaid(compiled)
        assert "router" in mermaid

    def test_builder_renders(self) -> None:
        from prismal.agents.extension import PrismalStateGraphBuilder

        async def _n(state: dict) -> dict:
            return {}

        b = PrismalStateGraphBuilder("demo")
        b.add_node("only", _n)
        b.set_entry_point("only")
        assert "only" in to_mermaid(b)

    def test_non_graph_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            to_mermaid(object())


class TestPng:
    def test_png_delegates_to_draw(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = SimpleNamespace(
            get_graph=lambda: SimpleNamespace(draw_mermaid_png=lambda: b"PNGBYTES")
        )
        monkeypatch.setattr(viz, "_as_compiled", lambda _o: fake)
        assert to_mermaid_png(object()) == b"PNGBYTES"

    def test_save_graph_image_writes_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(viz, "to_mermaid_png", lambda _o: b"PNGBYTES")
        out = tmp_path / "graph.png"
        save_graph_image(object(), out)
        assert out.read_bytes() == b"PNGBYTES"


class TestVisualize:
    def test_non_graph_raises(self) -> None:
        with pytest.raises(TypeError):
            visualize(object())

    def test_falls_back_to_mermaid_text(self, capsys: pytest.CaptureFixture[str]) -> None:
        # IPython is not installed in the base env, so the PNG/display path
        # raises ImportError and the helper prints the mermaid text instead.
        visualize(build_multimodal_subgraph())
        out = capsys.readouterr().out
        assert "Mermaid del grafo:" in out
        assert "router" in out

    def test_success_path_when_png_available(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Simulate a working IPython + PNG renderer.
        import sys
        import types

        fake_ip = types.ModuleType("IPython.display")
        fake_ip.Image = lambda *_a, **_k: object()  # type: ignore[attr-defined]
        fake_ip.display = lambda *_a, **_k: None  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "IPython", types.ModuleType("IPython"))
        monkeypatch.setitem(sys.modules, "IPython.display", fake_ip)
        fake = SimpleNamespace(
            get_graph=lambda: SimpleNamespace(
                draw_mermaid_png=lambda: b"PNG", draw_mermaid=lambda: "M"
            )
        )
        monkeypatch.setattr(viz, "_as_compiled", lambda _o: fake)
        visualize(object())
        assert "✅" in capsys.readouterr().out
