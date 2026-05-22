"""FAQ-retrieval node for customer_service subgraph.

Uses the injected RAG engine to search the FAQ/knowledge-base for the
latest user query. Records retrieved chunks and a confidence score
(``max(relevance_score)`` over the hits; ``0.0`` if no hits) into
``state["metadata"]["customer_service"]``.

If ``rag_engine=None`` the node short-circuits: no retrieval runs, and
confidence is set to ``0.0`` — the escalation gate will then send the flow
to ``ticket_creator``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = get_logger("lightagent.subgraphs.customer_service.faq_retrieval")


def make_faq_retrieval_node(
    rag_engine: Any | None,
    k: int = 5,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return an async LangGraph node that retrieves from the RAG engine."""

    async def faq_retrieval_node(state: dict[str, Any]) -> dict[str, Any]:
        messages = state.get("messages") or []
        if not messages:
            return {}
        query = str(messages[-1].content)

        existing = dict(state.get("metadata") or {}).get("customer_service") or {}

        if rag_engine is None:
            logger.info("customer_service_faq_skip", reason="no_rag_engine")
            updated_cs = {**existing, "retrieved": [], "confidence": 0.0}
            return {
                "metadata": {
                    **(state.get("metadata") or {}),
                    "customer_service": updated_cs,
                }
            }

        try:
            chunks = rag_engine.search(query, k=k)
        except Exception as exc:
            logger.warning("customer_service_faq_error", error=str(exc))
            chunks = []

        if chunks:
            confidence = max(float(getattr(c, "relevance_score", 0.0)) for c in chunks)
        else:
            confidence = 0.0

        logger.info(
            "customer_service_faq_retrieved",
            n_chunks=len(chunks),
            confidence=confidence,
        )
        updated_cs = {
            **existing,
            "retrieved": list(chunks),
            "confidence": confidence,
        }
        return {
            "metadata": {
                **(state.get("metadata") or {}),
                "customer_service": updated_cs,
            }
        }

    return faq_retrieval_node


__all__ = ["make_faq_retrieval_node"]
