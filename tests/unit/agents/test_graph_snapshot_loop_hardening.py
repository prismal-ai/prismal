"""Graph snapshot guard for loop hardening (Phase LH — RF-LH-009).

Neither LH1 (context compaction) nor LH2 (tool gating) inserts a new
node/edge into the supervisor graph — both are consumed inside existing
seams (``supervisor_node``'s return-value folding and the tool-resolution
call sites, respectively). This asserts the supervisor graph is byte-for-byte
identical whether ``context_compaction_enabled``/``tool_gating_enabled`` are
True or False.
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

    def __getattr__(self, _name: str) -> object:  # pragma: no cover - defensive
        return False


class _LoopHardeningOn(_Settings):
    context_compaction_enabled = True
    tool_gating_enabled = True


def test_graph_snapshot_unchanged_when_loop_hardening_toggled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(graph_module, "get_settings", _Settings)
    baseline = build_supervisor_graph(checkpoint_path=tmp_path / "off.db").get_graph()

    monkeypatch.setattr(graph_module, "get_settings", _LoopHardeningOn)
    candidate = build_supervisor_graph(checkpoint_path=tmp_path / "on.db").get_graph()

    assert set(baseline.nodes) == set(candidate.nodes)
    assert {(e.source, e.target) for e in baseline.edges} == {
        (e.source, e.target) for e in candidate.edges
    }
    assert baseline.draw_mermaid() == candidate.draw_mermaid()
