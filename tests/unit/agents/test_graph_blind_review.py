"""Graph-level wiring of the blind_review_pipeline node (Phase BRP5-02/03)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from prismal.agents import graph as graph_module
from prismal.agents.graph import build_supervisor_graph


class _Settings:
    """Minimal settings stub toggling only the flags graph.py reads."""

    enable_subgraphs = False
    multimodal_enabled = False
    kokoro_enabled = False
    skynet_enabled = False
    blind_review_pipeline_enabled = True
    hierarchical_mode = False

    def __getattr__(self, _name: str) -> object:  # pragma: no cover - defensive
        return False


async def _fake_brp(state: dict[str, Any]) -> dict[str, Any]:
    return {}


class TestBuildBlindReviewNodes:
    async def test_returns_compiled_pipeline(self) -> None:
        nodes = await graph_module._build_blind_review_nodes()
        assert "blind_review_pipeline" in nodes
        assert nodes["blind_review_pipeline"] is not None


class TestGraphWiring:
    def test_node_added_when_enabled(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(graph_module, "get_settings", _Settings)

        compiled = build_supervisor_graph(
            checkpoint_path=tmp_path / "cp.db",
            blind_review_nodes={"blind_review_pipeline": _fake_brp},
        )
        assert "blind_review_pipeline" in compiled.get_graph().nodes

    def test_node_absent_when_disabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Off(_Settings):
            blind_review_pipeline_enabled = False

        monkeypatch.setattr(graph_module, "get_settings", lambda: _Off())

        compiled = build_supervisor_graph(
            checkpoint_path=tmp_path / "cp2.db",
            blind_review_nodes={"blind_review_pipeline": _fake_brp},
        )
        assert "blind_review_pipeline" not in compiled.get_graph().nodes


def test_blind_review_absent_from_prompt_when_disabled() -> None:
    """build_system_prompt gates the blind-review section on the flag (BRP5-03)."""
    from prismal.agents.supervisor import build_system_prompt

    off = build_system_prompt(False, False, False, False, enable_blind_review=False)
    on = build_system_prompt(False, False, False, False, enable_blind_review=True)

    assert "blind_review_pipeline" not in off
    assert "blind_review_pipeline" in on
