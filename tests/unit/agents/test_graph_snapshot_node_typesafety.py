"""Graph snapshot guard for node typesafety (Phase NTS — RF-NTS-008).

``node_io_validation_middleware`` is consumed inside the existing ``@prismal_node``
seam; it inserts no node/edge into the supervisor graph. This asserts the
supervisor graph is byte-for-byte identical whether ``node_typesafety_enabled``
is True or False (mirrors ``test_graph_snapshot_loop_hardening``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prismal.agents import graph as graph_module
from prismal.agents.graph import build_supervisor_graph


class _Settings:
    enable_subgraphs = False
    multimodal_enabled = False
    kokoro_enabled = False
    skynet_enabled = False
    hierarchical_mode = False
    budget_enabled = False
    context_compaction_enabled = False
    tool_gating_enabled = False
    node_typesafety_enabled = False
    node_typesafety_mode = "warn"

    def __getattr__(self, _name: str) -> object:  # pragma: no cover - defensive
        return False


class _NodeTypesafetyOn(_Settings):
    node_typesafety_enabled = True
    node_typesafety_mode = "enforce"


def test_graph_snapshot_unchanged_when_node_typesafety_toggled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(graph_module, "get_settings", _Settings)
    baseline = build_supervisor_graph(checkpoint_path=tmp_path / "off.db").get_graph()

    monkeypatch.setattr(graph_module, "get_settings", _NodeTypesafetyOn)
    candidate = build_supervisor_graph(checkpoint_path=tmp_path / "on.db").get_graph()

    assert set(baseline.nodes) == set(candidate.nodes)
    assert {(e.source, e.target) for e in baseline.edges} == {
        (e.source, e.target) for e in candidate.edges
    }
    assert baseline.draw_mermaid() == candidate.draw_mermaid()
