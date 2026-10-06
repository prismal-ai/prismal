"""Graph snapshot guard for adaptive routing (Phase AMR — SPEC-AMR-SNP-001, AMR6-06).

Routing only picks the model string inside existing nodes: no node, edge or
route is added, so the supervisor graph is identical in every mode.
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
    token_efficiency_enabled = False
    llm_gateways_enabled = False
    decisions_enabled = False
    adaptive_routing_enabled = False
    plan_cache_enabled = False
    response_cache_enabled = False
    batch_api_enabled = False

    def __getattr__(self, _name: str) -> object:  # pragma: no cover - defensive
        return False


class _RoutingOn(_Settings):
    adaptive_routing_enabled = True
    routing_mode = "enforce"
    routing_policy = "composite"
    plan_cache_enabled = True
    response_cache_enabled = True
    batch_api_enabled = True


def test_graph_snapshot_unchanged_when_routing_toggled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(graph_module, "get_settings", _Settings)
    baseline = build_supervisor_graph(checkpoint_path=tmp_path / "off.db").get_graph()

    monkeypatch.setattr(graph_module, "get_settings", _RoutingOn)
    candidate = build_supervisor_graph(checkpoint_path=tmp_path / "on.db").get_graph()

    assert set(baseline.nodes) == set(candidate.nodes)
    assert {(e.source, e.target) for e in baseline.edges} == {
        (e.source, e.target) for e in candidate.edges
    }
    assert baseline.draw_mermaid() == candidate.draw_mermaid()
