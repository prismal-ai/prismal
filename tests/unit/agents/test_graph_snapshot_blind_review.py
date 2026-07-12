"""Graph snapshot guard for the Blind Review Pipeline (Phase BRP5-04).

With ``blind_review_pipeline_enabled=False`` the compiled supervisor topology
must be byte-for-byte identical to the pre-BRP graph — the node is only wired
when the flag is on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from prismal.agents import graph as graph_module
from prismal.agents.graph import build_supervisor_graph


class _Settings:
    """Minimal settings stub with the blind-review flag OFF."""

    enable_subgraphs = False
    multimodal_enabled = False
    kokoro_enabled = False
    skynet_enabled = False
    blind_review_pipeline_enabled = False
    hierarchical_mode = False

    def __getattr__(self, _name: str) -> object:  # pragma: no cover - defensive
        return False


async def _fake_brp(state: dict[str, Any]) -> dict[str, Any]:
    return {}


def test_graph_snapshot_unchanged_with_blind_review_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(graph_module, "get_settings", _Settings)

    baseline = build_supervisor_graph(checkpoint_path=tmp_path / "a.db").get_graph()
    # Passing the node map must be a no-op while the flag is off.
    candidate = build_supervisor_graph(
        checkpoint_path=tmp_path / "b.db",
        blind_review_nodes={"blind_review_pipeline": _fake_brp},
    ).get_graph()

    assert set(baseline.nodes) == set(candidate.nodes)
    assert {(e.source, e.target) for e in baseline.edges} == {
        (e.source, e.target) for e in candidate.edges
    }
    assert baseline.draw_mermaid() == candidate.draw_mermaid()
