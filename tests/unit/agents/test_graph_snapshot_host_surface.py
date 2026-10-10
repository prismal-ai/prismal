"""Graph snapshot guard for the host read surface (HRS — SPEC-HRS-GRD-001).

HRS adds read methods on ``RuntimeContext`` and in-process recorders only: no
node, edge or route, so the supervisor graph is identical with the surface in
use (recorders fed, retention changed).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prismal.agents import graph as graph_module
from prismal.agents.graph import build_supervisor_graph

pytestmark = pytest.mark.unit


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
    host_surface_stats_retention_h = 168

    def __getattr__(self, _name: str) -> object:  # pragma: no cover - defensive
        return False


class _SurfaceInUse(_Settings):
    host_surface_stats_retention_h = 1


def test_graph_snapshot_unchanged_with_host_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(graph_module, "get_settings", _Settings)
    baseline = build_supervisor_graph(checkpoint_path=tmp_path / "off.db").get_graph()

    from prismal.composition import build_test_runtime
    from prismal.monitoring.host_stats import record_metric

    record_metric("routing_decisions", 1, {"policy": "static", "tier": "small"})
    build_test_runtime().verify_audit_chain()
    monkeypatch.setattr(graph_module, "get_settings", _SurfaceInUse)
    candidate = build_supervisor_graph(checkpoint_path=tmp_path / "on.db").get_graph()

    assert set(baseline.nodes) == set(candidate.nodes)
    assert {(e.source, e.target) for e in baseline.edges} == {
        (e.source, e.target) for e in candidate.edges
    }
    assert baseline.draw_mermaid() == candidate.draw_mermaid()
