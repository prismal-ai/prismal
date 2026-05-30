"""Tests for SubgraphDefinition visualization convenience methods (V2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from prismal.agents import visualization as viz
from prismal.agents.subgraphs.multimodal_pipeline import build_multimodal_subgraph


class TestSubgraphDefinitionViz:
    def test_to_mermaid_method(self) -> None:
        mermaid = build_multimodal_subgraph().to_mermaid()
        assert isinstance(mermaid, str)
        assert "router" in mermaid
        assert "fusion_node" in mermaid

    def test_visualize_falls_back_to_text(self, capsys: pytest.CaptureFixture[str]) -> None:
        # IPython absent in the base env → prints the Mermaid text.
        build_multimodal_subgraph().visualize()
        out = capsys.readouterr().out
        assert "Mermaid del grafo:" in out
        assert "router" in out

    def test_save_image_method(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(viz, "to_mermaid_png", lambda _o: b"PNGBYTES")
        out = tmp_path / "mm.png"
        build_multimodal_subgraph().save_image(out)
        assert out.read_bytes() == b"PNGBYTES"

    def test_methods_delegate_to_module(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The method must funnel through the shared module helper.
        called: dict[str, object] = {}

        def _fake(obj: object) -> str:
            called["obj"] = obj
            return "M"

        monkeypatch.setattr(viz, "to_mermaid", _fake)
        definition = build_multimodal_subgraph()
        assert definition.to_mermaid() == "M"
        assert called["obj"] is definition
