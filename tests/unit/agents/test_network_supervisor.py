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
    with patch("lightagent.agents.network_supervisor.get_compiled_graph") as mock_graph_fn:
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
        patch("lightagent.agents.network_supervisor.get_compiled_graph") as mock_graph_fn,
    ):
        mock_httpx.AsyncClient.return_value.__aenter__.return_value.post = AsyncMock(
            side_effect=Exception("connection refused")
        )
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value=mock_local_result)
        mock_graph_fn.return_value = mock_graph

        result = await agent.delegate(task="explain Python", capability="research")

    assert result == mock_local_result


def test_load_nodes_returns_empty_when_config_missing(tmp_path) -> None:
    """_load_nodes returns [] when the config file does not exist."""

    non_existent = tmp_path / "does_not_exist.yaml"
    nodes = _load_nodes(non_existent)
    assert nodes == []


def test_find_node_returns_none_when_no_match() -> None:
    """_find_node returns None when no node has the requested capability."""
    node = NetworkNode(
        name="data-node",
        url="http://data:8000",
        capabilities=["data_analysis"],
        enabled=True,
    )
    agent = NetworkSupervisorAgent(nodes=[node])
    result = agent._find_node("research")
    assert result is None


def test_find_node_returns_matching_node() -> None:
    """_find_node returns the first node that has the requested capability."""
    node1 = NetworkNode(
        name="research-node",
        url="http://research:8000",
        capabilities=["research", "rag"],
        enabled=True,
    )
    node2 = NetworkNode(
        name="code-node",
        url="http://code:8000",
        capabilities=["coding"],
        enabled=True,
    )
    agent = NetworkSupervisorAgent(nodes=[node1, node2])
    result = agent._find_node("rag")
    assert result is not None
    assert result.name == "research-node"


def test_network_supervisor_loads_nodes_from_config(tmp_path) -> None:
    """NetworkSupervisorAgent loads nodes from YAML when nodes=None."""
    cfg = tmp_path / "network_nodes.yaml"
    cfg.write_text(
        "nodes:\n"
        "  - name: auto-node\n"
        "    url: http://auto:8000\n"
        "    capabilities: [rag]\n"
        "    enabled: true\n"
    )
    agent = NetworkSupervisorAgent(config_path=cfg)
    assert len(agent._nodes) == 1
    assert agent._nodes[0].name == "auto-node"


def test_make_a2a_jwt_returns_signed_token() -> None:
    """_make_a2a_jwt returns a non-empty signed JWT string when jose is available."""
    from unittest.mock import MagicMock

    from lightagent.agents.network_supervisor import _make_a2a_jwt

    mock_settings = MagicMock()
    mock_settings.jwt_secret_key.get_secret_value.return_value = "test-secret-key-long-enough"

    with patch("lightagent.core.config.get_settings", return_value=mock_settings):
        result = _make_a2a_jwt("http://node:8000")

    assert isinstance(result, str)
    assert len(result) > 0


@pytest.mark.asyncio
async def test_delegate_succeeds_with_remote_node() -> None:
    """NetworkSupervisorAgent calls remote node and returns parsed response."""
    node = NetworkNode(
        name="good-node",
        url="http://good-host:8000",
        capabilities=["research"],
        enabled=True,
        timeout_seconds=10,
    )
    agent = NetworkSupervisorAgent(nodes=[node])

    mock_response = MagicMock()
    mock_response.json.return_value = {"reply": "remote answer"}
    mock_response.raise_for_status = MagicMock()

    with (
        patch("lightagent.agents.network_supervisor.httpx") as mock_httpx,
        patch("lightagent.agents.network_supervisor._make_a2a_jwt", return_value="tok"),
    ):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_httpx.AsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_httpx.AsyncClient.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await agent.delegate(task="research AI", capability="research")

    assert "messages" in result
    assert result["current_agent"] == "network_supervisor"
