"""Tests for advanced-architecture node wiring in the supervisor graph (Phase D / D1-01).

``build_supervisor_graph`` accepts an ``advanced_nodes`` mapping of
node-name -> node (pattern callables or compiled subgraphs). They are added to
the graph only when ``settings.enable_subgraphs`` is on, mirroring how the
existing dev/ml/financial subgraphs are gated — so the default graph is
unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from prismal.agents.graph import build_supervisor_graph
from prismal.core.config import get_settings


async def _dummy_node(_state: dict[str, Any]) -> dict[str, Any]:
    return {}


def test_advanced_nodes_wired_when_enabled(tmp_path: Path) -> None:
    advanced = {"tot_agent": _dummy_node, "code_review": _dummy_node}
    settings = get_settings().model_copy(update={"enable_subgraphs": True})
    with patch("prismal.agents.graph.get_settings", return_value=settings):
        graph = build_supervisor_graph(checkpoint_path=tmp_path / "adv.db", advanced_nodes=advanced)
    mermaid = graph.get_graph().draw_mermaid()
    assert "tot_agent" in mermaid
    assert "code_review" in mermaid


def test_advanced_nodes_ignored_when_disabled(tmp_path: Path) -> None:
    advanced = {"tot_agent": _dummy_node}
    settings = get_settings().model_copy(update={"enable_subgraphs": False})
    with patch("prismal.agents.graph.get_settings", return_value=settings):
        graph = build_supervisor_graph(
            checkpoint_path=tmp_path / "adv_off.db", advanced_nodes=advanced
        )
    mermaid = graph.get_graph().draw_mermaid()
    assert "tot_agent" not in mermaid


def test_default_graph_has_no_advanced_nodes(tmp_path: Path) -> None:
    """Without advanced_nodes the graph is unchanged (zero regression)."""
    graph = build_supervisor_graph(checkpoint_path=tmp_path / "plain.db")
    mermaid = graph.get_graph().draw_mermaid()
    assert "tot_agent" not in mermaid
    assert "code_review" not in mermaid
    # Base agent still present.
    assert "researcher" in mermaid


import pytest  # noqa: E402

from prismal.agents.supervisor import ADVANCED_MEMBERS  # noqa: E402


@pytest.mark.asyncio
async def test_build_advanced_nodes_covers_all_members() -> None:
    """_build_advanced_nodes returns one node per ADVANCED_MEMBERS name."""
    from prismal.agents.graph import _build_advanced_nodes

    with patch("prismal.providers.registry.ProviderRegistry") as mock_registry:
        mock_registry.return_value.get_llm.return_value = object()
        nodes = await _build_advanced_nodes()

    assert set(nodes) == set(ADVANCED_MEMBERS)
    assert len(nodes) == 11
