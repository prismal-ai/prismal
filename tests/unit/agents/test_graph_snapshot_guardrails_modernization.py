"""Graph snapshot guard for guardrails modernization (Phase GRD — RF-GRD-012).

Neither GRD1 (NeMo classifier) nor GRD2 (StructuredOutputGuard) inserts a new
node/edge into the supervisor graph — both are consumed inside existing seams
(``GuardrailsEngine``/``NemoRailsLayer`` and the output-validation call site,
respectively). This asserts the supervisor graph is byte-for-byte identical
whether ``nemo_classifier_enabled``/``structured_output_guard_enabled`` are
True or False.
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
    nemo_classifier_enabled = False
    structured_output_guard_enabled = False

    def __getattr__(self, _name: str) -> object:  # pragma: no cover - defensive
        return False


class _GuardrailsModernizationOn(_Settings):
    nemo_classifier_enabled = True
    structured_output_guard_enabled = True


def test_graph_snapshot_unchanged_when_guardrails_modernization_toggled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(graph_module, "get_settings", _Settings)
    baseline = build_supervisor_graph(checkpoint_path=tmp_path / "off.db").get_graph()

    monkeypatch.setattr(graph_module, "get_settings", _GuardrailsModernizationOn)
    candidate = build_supervisor_graph(checkpoint_path=tmp_path / "on.db").get_graph()

    assert set(baseline.nodes) == set(candidate.nodes)
    assert {(e.source, e.target) for e in baseline.edges} == {
        (e.source, e.target) for e in candidate.edges
    }
    assert baseline.draw_mermaid() == candidate.draw_mermaid()
