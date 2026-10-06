"""Graph snapshot guard for typed decisions (Phase DEC — SPEC-DEC-CMP-001, DEC1-11).

Decisions run inside existing nodes (DD-DEC-009): no node, edge or
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

    def __getattr__(self, _name: str) -> object:  # pragma: no cover - defensive
        return False


class _DecisionsOn(_Settings):
    decisions_enabled = True
    decision_routing_enabled = True
    decision_eval_judge_enabled = True
    decision_hitl_enabled = True
    decision_tool_proposal_enabled = True
    decision_rag_relevance_enabled = True
    decision_ctx_relevance_enabled = True
    decision_swarm_eval_enabled = True
    decision_safety_prescreen_enabled = True
    decision_modes = {"route": "enforce", "hitl": "shadow"}  # noqa: RUF012


def test_graph_snapshot_unchanged_when_decisions_toggled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(graph_module, "get_settings", _Settings)
    baseline = build_supervisor_graph(checkpoint_path=tmp_path / "off.db").get_graph()

    monkeypatch.setattr(graph_module, "get_settings", _DecisionsOn)
    candidate = build_supervisor_graph(checkpoint_path=tmp_path / "on.db").get_graph()

    assert set(baseline.nodes) == set(candidate.nodes)
    assert {(e.source, e.target) for e in baseline.edges} == {
        (e.source, e.target) for e in candidate.edges
    }
    assert baseline.draw_mermaid() == candidate.draw_mermaid()
