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
