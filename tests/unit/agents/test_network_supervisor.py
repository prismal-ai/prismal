"""Unit tests for lightagent.agents.network_supervisor."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lightagent.agents.network_supervisor import (
    NetworkNode,
    NetworkSupervisorAgent,
    _load_nodes,
)


def test_network_node_model() -> None:
    """NetworkNode validates fields correctly."""
    node = NetworkNode(
        name="test-node",
        url="http://localhost:8000",
        capabilities=["rag", "research"],
        enabled=True,
        timeout_seconds=30,
    )
    assert node.name == "test-node"
    assert "rag" in node.capabilities


def test_load_nodes_empty_config(tmp_path) -> None:
    """_load_nodes returns [] when nodes list is empty."""
    cfg = tmp_path / "network_nodes.yaml"
    cfg.write_text("nodes: []\n")
    nodes = _load_nodes(cfg)
    assert nodes == []


def test_load_nodes_parses_config(tmp_path) -> None:
    """_load_nodes correctly parses node entries."""
    cfg = tmp_path / "network_nodes.yaml"
    cfg.write_text(
        """
nodes:
  - name: node-1
    url: http://example.com:8000
    capabilities: [rag, research]
    enabled: true
    timeout_seconds: 10
  - name: node-2
    url: http://example.com:8001
    capabilities: [coding]
    enabled: false
    timeout_seconds: 20
"""
    )
    nodes = _load_nodes(cfg)
    assert len(nodes) == 1  # only enabled
    assert nodes[0].name == "node-1"


@pytest.mark.asyncio
async def test_delegate_falls_back_to_local_when_no_nodes() -> None:
    """NetworkSupervisorAgent falls back to local when no nodes available."""
    agent = NetworkSupervisorAgent(nodes=[])

    mock_local_result = {"messages": [MagicMock(content="local result")]}
    with patch(
        "lightagent.agents.network_supervisor.get_compiled_graph"
    ) as mock_graph_fn:
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value=mock_local_result)
        mock_graph_fn.return_value = mock_graph

        result = await agent.delegate(task="explain Python", capability="research")

    assert result == mock_local_result


@pytest.mark.asyncio
async def test_delegate_falls_back_on_http_error() -> None:
    """NetworkSupervisorAgent falls back to local on HTTP error."""
    node = NetworkNode(
        name="bad-node",
        url="http://dead-host:9999",
        capabilities=["research"],
        enabled=True,
        timeout_seconds=1,
    )
    agent = NetworkSupervisorAgent(nodes=[node])

    mock_local_result = {"messages": [MagicMock(content="local fallback")]}

    with (
        patch("lightagent.agents.network_supervisor.httpx") as mock_httpx,
        patch(
            "lightagent.agents.network_supervisor.get_compiled_graph"
        ) as mock_graph_fn,
    ):
        mock_httpx.AsyncClient.return_value.__aenter__.return_value.post = AsyncMock(
            side_effect=Exception("connection refused")
        )
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value=mock_local_result)
        mock_graph_fn.return_value = mock_graph

        result = await agent.delegate(task="explain Python", capability="research")

    assert result == mock_local_result
