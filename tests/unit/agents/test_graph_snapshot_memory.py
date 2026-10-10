"""Graph snapshot guard for agentic memory (Phase MEM — SPEC-MEM-SNP-001, MEM6-05).

Memory v2 is a port composed per tenant (RuntimeContext.memory) and read in agent seams: no node, edge or
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


class _MemoryOn(_Settings):
    memory_v2_enabled = True
    memory_mode = "enforce"
    memory_write_gate = "decision"
    memory_graph_backend = "sqlite"
    memory_playbooks_enabled = True


def test_graph_snapshot_unchanged_when_memory_flags_toggled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(graph_module, "get_settings", _Settings)
    baseline = build_supervisor_graph(checkpoint_path=tmp_path / "off.db").get_graph()

    monkeypatch.setattr(graph_module, "get_settings", _MemoryOn)
    candidate = build_supervisor_graph(checkpoint_path=tmp_path / "on.db").get_graph()

    assert set(baseline.nodes) == set(candidate.nodes)
    assert {(e.source, e.target) for e in baseline.edges} == {
        (e.source, e.target) for e in candidate.edges
    }
    assert baseline.draw_mermaid() == candidate.draw_mermaid()
