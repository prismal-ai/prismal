"""Graph snapshot guard for runtime hardening (Phase H — H6-06 / RF-HRD-009).

The hardening layer only changes node *bodies* (supervisor seeding, react_loop
screening/policy/runaway, the @prismal_node middleware) — never the compiled
topology. This asserts the supervisor graph is byte-for-byte identical whether
``hardening_enabled`` is True or False.
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
    hardening_enabled = False

    def __getattr__(self, _name: str) -> object:  # pragma: no cover - defensive
        return False


class _HardeningOn(_Settings):
    hardening_enabled = True


def test_graph_snapshot_unchanged_when_hardening_toggled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(graph_module, "get_settings", _Settings)
    baseline = build_supervisor_graph(checkpoint_path=tmp_path / "off.db").get_graph()

    monkeypatch.setattr(graph_module, "get_settings", _HardeningOn)
    candidate = build_supervisor_graph(checkpoint_path=tmp_path / "on.db").get_graph()

    assert set(baseline.nodes) == set(candidate.nodes)
    assert {(e.source, e.target) for e in baseline.edges} == {
        (e.source, e.target) for e in candidate.edges
    }
    assert baseline.draw_mermaid() == candidate.draw_mermaid()
