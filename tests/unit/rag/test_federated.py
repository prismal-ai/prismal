"""Unit tests for lightagent.rag.federated."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lightagent.rag.federated import FederatedRAGEngine, _merge_and_rerank


def test_merge_and_rerank_deduplicates_by_chunk_id() -> None:
    """_merge_and_rerank removes duplicate chunk IDs, keeping highest score."""
    from lightagent.rag.crag import RetrievedChunk

    chunks = [
        RetrievedChunk(
            source="a.txt", chunk_id="c1", relevance_score=0.9, content="hello"
        ),
        RetrievedChunk(
            source="b.txt", chunk_id="c1", relevance_score=0.7, content="hello"
        ),
        RetrievedChunk(
            source="c.txt", chunk_id="c2", relevance_score=0.8, content="world"
        ),
    ]
    merged = _merge_and_rerank(chunks)
    assert len(merged) == 2
    # c1 kept with highest score 0.9
    c1 = next(c for c in merged if c.chunk_id == "c1")
    assert c1.relevance_score == 0.9


def test_merge_and_rerank_sorts_by_score_desc() -> None:
    """_merge_and_rerank returns chunks sorted by relevance_score descending."""
    from lightagent.rag.crag import RetrievedChunk

    chunks = [
        RetrievedChunk(
            source="a.txt", chunk_id="c1", relevance_score=0.5, content="a"
        ),
        RetrievedChunk(
            source="b.txt", chunk_id="c2", relevance_score=0.9, content="b"
        ),
        RetrievedChunk(
            source="c.txt", chunk_id="c3", relevance_score=0.7, content="c"
        ),
    ]
    merged = _merge_and_rerank(chunks)
    scores = [c.relevance_score for c in merged]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_federated_search_empty_nodes_returns_local() -> None:
    """FederatedRAGEngine with no remote nodes returns local results only."""
    from lightagent.rag.crag import RetrievedChunk

    local_chunk = RetrievedChunk(
        source="local.txt", chunk_id="l1", relevance_score=0.8, content="local"
    )

    mock_local_engine = MagicMock()
    mock_local_engine.search.return_value = [local_chunk]

    engine = FederatedRAGEngine(local_engine=mock_local_engine, remote_nodes=[])
    results = await engine.search("test query", k=5)

    assert len(results) == 1
    assert results[0].source == "local.txt"


@pytest.mark.asyncio
async def test_federated_search_merges_remote_results() -> None:
    """FederatedRAGEngine merges local + remote results and re-ranks."""
    from lightagent.rag.crag import RetrievedChunk

    local_chunk = RetrievedChunk(
        source="local.txt", chunk_id="l1", relevance_score=0.5, content="local"
    )
    remote_chunk = RetrievedChunk(
        source="remote.txt", chunk_id="r1", relevance_score=0.9, content="remote"
    )

    mock_local_engine = MagicMock()
    mock_local_engine.search.return_value = [local_chunk]

    engine = FederatedRAGEngine(
        local_engine=mock_local_engine,
        remote_nodes=[{"url": "http://node1:8000", "name": "node1"}],
    )

    with patch.object(
        engine, "_query_remote_node", AsyncMock(return_value=[remote_chunk])
    ):
        results = await engine.search("test query", k=5)

    assert len(results) == 2
    # Sorted desc by score: remote first
    assert results[0].chunk_id == "r1"


@pytest.mark.asyncio
async def test_federated_search_skips_failed_nodes() -> None:
    """FederatedRAGEngine skips nodes that raise exceptions (partial results)."""
    from lightagent.rag.crag import RetrievedChunk

    local_chunk = RetrievedChunk(
        source="local.txt", chunk_id="l1", relevance_score=0.5, content="local"
    )

    mock_local_engine = MagicMock()
    mock_local_engine.search.return_value = [local_chunk]

    engine = FederatedRAGEngine(
        local_engine=mock_local_engine,
        remote_nodes=[{"url": "http://dead:9999", "name": "dead-node"}],
    )

    with patch.object(
        engine,
        "_query_remote_node",
        AsyncMock(side_effect=Exception("connection refused")),
    ):
        results = await engine.search("test query", k=5)

    # Local results still returned despite remote failure
    assert len(results) == 1
    assert results[0].chunk_id == "l1"


# ── FederatedRAGEngine — __init__ with default local engine ──────────────────


def test_federated_default_local_engine_created() -> None:
    """FederatedRAGEngine creates a default RAGEngine when local_engine=None."""
    from unittest.mock import MagicMock, patch

    mock_rag = MagicMock()
    with patch("lightagent.rag.engine.RAGEngine", return_value=mock_rag) as mock_cls:
        engine = FederatedRAGEngine(remote_nodes=[])

    mock_cls.assert_called_once()
    assert engine._local is mock_rag


def test_federated_default_remote_nodes_loaded() -> None:
    """FederatedRAGEngine loads remote_nodes from _load_rag_nodes when None."""
    from unittest.mock import MagicMock, patch

    mock_local = MagicMock()
    fake_nodes = [{"url": "http://n1:8000", "name": "n1"}]

    with patch.object(FederatedRAGEngine, "_load_rag_nodes", return_value=fake_nodes):
        engine = FederatedRAGEngine(local_engine=mock_local)

    assert engine._nodes == fake_nodes


# ── FederatedRAGEngine._load_rag_nodes ────────────────────────────────────────


def test_load_rag_nodes_returns_nodes_with_rag_capability() -> None:
    """_load_rag_nodes returns only nodes that have 'rag' in capabilities."""
    from unittest.mock import MagicMock, patch

    from lightagent.agents.network_supervisor import NetworkNode

    nodes = [
        NetworkNode(
            name="rag-node",
            url="http://rag:8000",
            capabilities=["rag", "search"],
            enabled=True,
            timeout_seconds=30,
        ),
        NetworkNode(
            name="code-node",
            url="http://code:8000",
            capabilities=["coding"],
            enabled=True,
            timeout_seconds=30,
        ),
    ]

    with patch("lightagent.agents.network_supervisor._load_nodes", return_value=nodes):
        result = FederatedRAGEngine._load_rag_nodes()

    assert len(result) == 1
    assert result[0]["name"] == "rag-node"
    assert result[0]["url"] == "http://rag:8000"


def test_load_rag_nodes_returns_empty_on_exception() -> None:
    """_load_rag_nodes returns [] when _load_nodes raises an exception."""
    from unittest.mock import patch

    with patch(
        "lightagent.agents.network_supervisor._load_nodes",
        side_effect=RuntimeError("config not found"),
    ):
        result = FederatedRAGEngine._load_rag_nodes()

    assert result == []


# ── FederatedRAGEngine._query_remote_node ─────────────────────────────────────


@pytest.mark.asyncio
async def test_query_remote_node_raises_when_httpx_missing() -> None:
    """_query_remote_node raises RuntimeError when httpx is not installed."""
    from unittest.mock import MagicMock, patch

    mock_local = MagicMock()
    engine = FederatedRAGEngine(local_engine=mock_local, remote_nodes=[])

    with patch("lightagent.rag.federated.httpx", None):
        with pytest.raises(RuntimeError, match="httpx not installed"):
            await engine._query_remote_node(
                {"url": "http://test:8000", "name": "node"}, "query", 5
            )


@pytest.mark.asyncio
async def test_query_remote_node_returns_parsed_chunks() -> None:
    """_query_remote_node returns RetrievedChunk objects from JSON response."""
    from unittest.mock import AsyncMock, MagicMock, patch

    mock_local = MagicMock()
    engine = FederatedRAGEngine(local_engine=mock_local, remote_nodes=[])

    node = {"url": "http://test:8000", "name": "test-node"}

    mock_response = MagicMock()
    mock_response.json.return_value = [
        {
            "source": "remote.txt",
            "chunk_id": "r1",
            "relevance_score": 0.8,
            "content": "remote content",
        }
    ]
    mock_response.raise_for_status = MagicMock()

    mock_async_client = AsyncMock()
    mock_async_client.get = AsyncMock(return_value=mock_response)
    mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
    mock_async_client.__aexit__ = AsyncMock(return_value=False)

    mock_httpx = MagicMock()
    mock_httpx.AsyncClient.return_value = mock_async_client

    with (
        patch("lightagent.rag.federated.httpx", mock_httpx),
        patch(
            "lightagent.agents.network_supervisor._make_a2a_jwt",
            return_value="test-token",
        ),
    ):
        chunks = await engine._query_remote_node(node, "test query", 5)

    assert len(chunks) == 1
    assert chunks[0].chunk_id == "r1"
    assert chunks[0].relevance_score == 0.8
    assert chunks[0].content == "remote content"


# ── FederatedRAGEngine.search — local error path ──────────────────────────────


@pytest.mark.asyncio
async def test_federated_search_handles_local_error() -> None:
    """search() returns empty list when local engine raises an exception."""
    from unittest.mock import MagicMock

    mock_local = MagicMock()
    mock_local.search.side_effect = RuntimeError("chroma unavailable")

    engine = FederatedRAGEngine(local_engine=mock_local, remote_nodes=[])
    results = await engine.search("test query", k=5)

    assert results == []
