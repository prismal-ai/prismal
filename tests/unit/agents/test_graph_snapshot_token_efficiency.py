"""Graph snapshot guard for token efficiency (Phase TKE — SPEC-TKE-COMPAT-001, TKE9-01).

No TKE sub-feature inserts a node or edge: caching/effort wrap the LLM,
gating/deferral/budgets live inside ``react_loop`` and the node middleware,
masking reuses the LH compaction seam, envelopes ride on existing results.
The supervisor graph must be byte-for-byte identical with every TKE flag on
or off.
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
    prompt_cache_enabled = False
    tool_defer_enabled = False
    tool_output_budget_enabled = False
    llm_effort_enabled = False
    handoff_envelope_enabled = False
    rag_context_budget_enabled = False

    def __getattr__(self, _name: str) -> object:  # pragma: no cover - defensive
        return False


class _TokenEfficiencyOn(_Settings):
    token_efficiency_enabled = True
    prompt_cache_enabled = True
    tool_defer_enabled = True
    tool_output_budget_enabled = True
    llm_effort_enabled = True
    handoff_envelope_enabled = True
    rag_context_budget_enabled = True
    tool_gating_mode = "mask"
    prompt_layout = "cache_stable"
    context_compaction_strategy = "mask_observations"


def test_graph_snapshot_unchanged_when_token_efficiency_toggled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(graph_module, "get_settings", _Settings)
    baseline = build_supervisor_graph(checkpoint_path=tmp_path / "off.db").get_graph()

    monkeypatch.setattr(graph_module, "get_settings", _TokenEfficiencyOn)
    candidate = build_supervisor_graph(checkpoint_path=tmp_path / "on.db").get_graph()

    assert set(baseline.nodes) == set(candidate.nodes)
    assert {(e.source, e.target) for e in baseline.edges} == {
        (e.source, e.target) for e in candidate.edges
    }
    assert baseline.draw_mermaid() == candidate.draw_mermaid()
