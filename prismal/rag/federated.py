"""Federated Knowledge Base — multi-node RAG search (T-213).

Queries RAG endpoints on multiple remote Prismal nodes in parallel,
merges results with local ChromaDB, and re-ranks by relevance score.

Architecture
------------
``FederatedRAGEngine`` composes:

* A local :class:`~prismal.rag.engine.RAGEngine` for the local ChromaDB.
* A list of remote node URLs read from ``config/network_nodes.yaml`` (the
  same registry as :mod:`prismal.agents.network_supervisor`).

Each remote node must expose ``GET /api/v1/rag/search?query=...&k=...``
with JWT bearer auth.  Failed nodes are skipped — partial results are
returned rather than raising.

SPEC-021 / T-213 acceptance criteria:
- ``FederatedRAGEngine`` queries remote nodes in parallel (``asyncio.gather``).
- Results merged and re-ranked by ``relevance_score`` descending.
- Failed node queries are skipped (partial results returned).
- ``prismal rag search --federated QUERY`` uses this engine.
- Remote node auth via JWT (SPEC-018 RBAC).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from prismal.core.logging import get_logger
from prismal.monitoring.otel import OTelManager
from prismal.rag.crag import RetrievedChunk

if TYPE_CHECKING:
    from prismal.rag.engine import RAGEngine

logger = get_logger("prismal.rag.federated")

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]


def _merge_and_rerank(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """Deduplicate by chunk_id (keep highest score) and sort descending.

    Args:
        chunks: Combined list from all nodes.

    Returns:
        Deduplicated and sorted list.
    """
    best: dict[str, RetrievedChunk] = {}
    for chunk in chunks:
        existing = best.get(chunk.chunk_id)
        if existing is None or chunk.relevance_score > existing.relevance_score:
            best[chunk.chunk_id] = chunk
    return sorted(best.values(), key=lambda c: c.relevance_score, reverse=True)


class FederatedRAGEngine:
    """Queries local + remote RAG nodes and merges results.

    Args:
        local_engine: The local :class:`~prismal.rag.engine.RAGEngine`.
            If None, a default engine targeting ``"default"`` collection is used.
        remote_nodes: List of node dicts with ``url`` and ``name`` keys.
            If None, loaded from ``config/network_nodes.yaml`` nodes that
            include ``"rag"`` in their capabilities.
        timeout_seconds: HTTP timeout for remote node requests.
    """

    def __init__(
        self,
        local_engine: RAGEngine | None = None,
        remote_nodes: list[dict[str, Any]] | None = None,
        timeout_seconds: int = 15,
    ) -> None:
        """Initialise the federated RAG engine."""
        if local_engine is not None:
            self._local = local_engine
        else:
            from prismal.rag.engine import RAGEngine

            self._local = RAGEngine()

        if remote_nodes is not None:
            self._nodes = remote_nodes
        else:
            self._nodes = self._load_rag_nodes()

        self._timeout = timeout_seconds

    @staticmethod
    def _load_rag_nodes() -> list[dict[str, Any]]:
        """Load nodes with 'rag' capability from network_nodes.yaml.

        Returns:
            List of dicts with ``url`` and ``name`` keys.
        """
        try:
            from prismal.agents.network_supervisor import (
                _load_nodes,
            )

            nodes = _load_nodes()
            return [{"url": n.url, "name": n.name} for n in nodes if "rag" in n.capabilities]
        except Exception as exc:
            logger.warning("federated_rag_load_nodes_error", error=str(exc))
            return []

    async def _query_remote_node(
        self,
        node: dict[str, Any],
        query: str,
        k: int,
    ) -> list[RetrievedChunk]:
        """Query a single remote node's RAG search endpoint.

        Args:
            node: Dict with ``url`` and ``name``.
            query: Search query string.
            k: Number of results requested.

        Returns:
            List of :class:`RetrievedChunk` from the remote node.

        Raises:
            Exception: On connection error, timeout, or non-200 response.
        """
        if httpx is None:
            msg = "httpx not installed"
            raise RuntimeError(msg)

        from prismal.agents.network_supervisor import _make_a2a_jwt

        token = _make_a2a_jwt(node["url"])
        headers = {"Authorization": f"Bearer {token}"} if token else {}

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                f"{node['url']}/api/v1/rag/search",
                params={"query": query, "k": k},
                headers=headers,
            )
            resp.raise_for_status()
            data: list[dict[str, Any]] = resp.json()

        chunks = [
            RetrievedChunk(
                source=item.get("source", node["name"]),
                chunk_id=item.get("chunk_id", ""),
                relevance_score=float(item.get("relevance_score", 0.0)),
                content=item.get("content", ""),
            )
            for item in data
        ]
        logger.info(
            "federated_rag_remote_results",
            node=node["name"],
            count=len(chunks),
        )
        return chunks

    async def search(
        self,
        query: str,
        k: int = 5,
    ) -> list[RetrievedChunk]:
        """Search local and remote nodes, merge, and re-rank results.

        Args:
            query: Natural language search query.
            k: Number of results per node (final result may contain up to
               ``k * (1 + len(remote_nodes))`` unique chunks before dedup).

        Returns:
            Merged and re-ranked list of :class:`RetrievedChunk`.
        """

        otel = OTelManager()
        with otel.start_span("federated_rag.search") as span:
            span.set_attribute("prismal.query_length", len(query))
            span.set_attribute("prismal.node_count", len(self._nodes) + 1)

            # Local search (synchronous RAGEngine.search)
            try:
                local_results: list[RetrievedChunk] = self._local.search(query, k=k)
            except Exception as exc:
                logger.warning("federated_rag_local_error", error=str(exc))
                local_results = []

            # Remote searches in parallel
            remote_tasks = [self._query_remote_node(node, query, k) for node in self._nodes]

            remote_results_nested: list[list[RetrievedChunk] | BaseException] = list(
                await asyncio.gather(*remote_tasks, return_exceptions=True)
            )

            all_chunks: list[RetrievedChunk] = list(local_results)
            for i, result in enumerate(remote_results_nested):
                if isinstance(result, BaseException):
                    node_name = (
                        self._nodes[i].get("name", str(i)) if i < len(self._nodes) else str(i)
                    )
                    logger.warning(
                        "federated_rag_node_failed",
                        node=node_name,
                        error=str(result),
                    )
                else:
                    all_chunks.extend(result)

            merged = _merge_and_rerank(all_chunks)
            span.set_attribute("prismal.result_count", len(merged))
            logger.info(
                "federated_rag_merged",
                total_before=len(all_chunks),
                after_dedup=len(merged),
            )
            return merged


__all__ = ["FederatedRAGEngine", "_merge_and_rerank"]
