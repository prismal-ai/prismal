"""Graph snapshot guard for protocol upgrades (Phase PRT — SPEC-PRT-GRD-001, PRT6-03).

Protocol adapters live at the edges (A2A handler, MCP provider, AG-UI translator): no node, edge or
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


class _ProtocolsOn(_Settings):
    a2a_protocol_version = "1.0"
    a2a_accept_legacy = True
    mcp_protocol_mode = "auto"
    mcp_tool_cache_enabled = True
    mcp_tasks_enabled = True
    agui_events_enabled = True


def test_graph_snapshot_unchanged_when_protocol_flags_toggled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(graph_module, "get_settings", _Settings)
    baseline = build_supervisor_graph(checkpoint_path=tmp_path / "off.db").get_graph()

    monkeypatch.setattr(graph_module, "get_settings", _ProtocolsOn)
    candidate = build_supervisor_graph(checkpoint_path=tmp_path / "on.db").get_graph()

    assert set(baseline.nodes) == set(candidate.nodes)
    assert {(e.source, e.target) for e in baseline.edges} == {
        (e.source, e.target) for e in candidate.edges
    }
    assert baseline.draw_mermaid() == candidate.draw_mermaid()
