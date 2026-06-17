"""Graph snapshot guard for agent identity governance (Phase IDN — ID6).

Identity is a pre-action gate (consulted at the ActionInterceptor seam), not a
graph node or supervisor route — so it must never change the compiled topology.
This asserts the supervisor graph is byte-for-byte identical whether
``identity_enabled`` is True or False.
"""

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
    skynet_enabled = False
    hierarchical_mode = False
    budget_enabled = False
    identity_enabled = False

    def __getattr__(self, _name: str) -> object:  # pragma: no cover - defensive
        return False


class _IdentityOn(_Settings):
    identity_enabled = True


def test_graph_snapshot_unchanged_when_identity_toggled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(graph_module, "get_settings", _Settings)
    baseline = build_supervisor_graph(checkpoint_path=tmp_path / "off.db").get_graph()

    monkeypatch.setattr(graph_module, "get_settings", _IdentityOn)
    candidate = build_supervisor_graph(checkpoint_path=tmp_path / "on.db").get_graph()

    assert set(baseline.nodes) == set(candidate.nodes)
    assert {(e.source, e.target) for e in baseline.edges} == {
        (e.source, e.target) for e in candidate.edges
    }
    assert baseline.draw_mermaid() == candidate.draw_mermaid()
