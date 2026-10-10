"""Graph snapshot guard for gateways (Phase GW — SPEC-GW-SNAP-001, GW1-10).

Gateways live entirely in the provider layer (DD-GW-001): no node, edge or
route is added, so the supervisor graph is identical with gateways on or off.
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

    def __getattr__(self, _name: str) -> object:  # pragma: no cover - defensive
        return False


class _GatewaysOn(_Settings):
    llm_gateways_enabled = True
    openrouter_enabled = True
    opencode_zen_enabled = True
    opencode_go_enabled = True
    omniroute_enabled = True
    llm_gateway_agent_map = {"coder": "gw:opencode-go/glm-5"}  # noqa: RUF012


def test_graph_snapshot_unchanged_when_gateways_toggled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(graph_module, "get_settings", _Settings)
    baseline = build_supervisor_graph(checkpoint_path=tmp_path / "off.db").get_graph()

    monkeypatch.setattr(graph_module, "get_settings", _GatewaysOn)
    candidate = build_supervisor_graph(checkpoint_path=tmp_path / "on.db").get_graph()

    assert set(baseline.nodes) == set(candidate.nodes)
    assert {(e.source, e.target) for e in baseline.edges} == {
        (e.source, e.target) for e in candidate.edges
    }
    assert baseline.draw_mermaid() == candidate.draw_mermaid()
