"""Graph snapshot guard for Skynet S+ (SP5-03 / RF-SP-09).

S+ only changes node *bodies* (role resolution, metering, remote delegation) —
never the compiled topology and never a supervisor route. This asserts:

1. the main supervisor graph is byte-for-byte identical whether the S+ flags
   are on or off, and
2. the skynet subgraph topology (nodes / edges / entry) is invariant to the S+
   flags.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prismal.agents import graph as graph_module
from prismal.agents.graph import build_supervisor_graph
from prismal.agents.subgraphs.skynet import build_skynet_subgraph
from prismal.core.config import Settings


class _Settings:
    """Minimal settings stub toggling only the flags graph.py reads."""

    enable_subgraphs = False
    multimodal_enabled = False
    kokoro_enabled = False
    skynet_enabled = False
    hierarchical_mode = False
    budget_enabled = False

    def __getattr__(self, _name: str) -> object:  # pragma: no cover - defensive
        return False


class _SplusOn(_Settings):
    skynet_enabled = True
    skynet_specialists_enabled = True
    skynet_remote_workers_enabled = True


def test_snapshot_unchanged_with_splus_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The main supervisor graph is byte-for-byte identical with S+ on vs off."""
    monkeypatch.setattr(graph_module, "get_settings", _Settings)
    baseline = build_supervisor_graph(checkpoint_path=tmp_path / "off.db").get_graph()

    monkeypatch.setattr(graph_module, "get_settings", _SplusOn)
    candidate = build_supervisor_graph(checkpoint_path=tmp_path / "on.db").get_graph()

    assert set(baseline.nodes) == set(candidate.nodes)
    assert {(e.source, e.target) for e in baseline.edges} == {
        (e.source, e.target) for e in candidate.edges
    }
    assert baseline.draw_mermaid() == candidate.draw_mermaid()


def test_skynet_subgraph_topology_invariant_to_splus() -> None:
    """The skynet subgraph topology does not change when the S+ flags flip."""
    off = build_skynet_subgraph(settings=Settings(_env_file=None))  # type: ignore[call-arg]
    on = build_skynet_subgraph(
        settings=Settings(  # type: ignore[call-arg]
            _env_file=None,
            skynet_specialists_enabled=True,
            skynet_remote_workers_enabled=True,
            a2a_enabled=True,
        )
    )

    assert list(off.nodes) == list(on.nodes)
    assert off.entry_point == on.entry_point
    assert off.edges == on.edges
    assert set(off.conditional_edges) == set(on.conditional_edges)
    assert [src for src, _ in off.send_edges] == [src for src, _ in on.send_edges]
