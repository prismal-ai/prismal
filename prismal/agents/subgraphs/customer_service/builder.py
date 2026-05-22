"""Builder for the customer_service subgraph (SPEC-SUBGRAPH-001).

Pipeline shape::

    classifier -> faq_retrieval -> escalation_gate ─┬-> response_generator
                                                     └-> ticket_creator

- ``classifier``: LLM categorises the user's query (faq/complaint/technical/
  other).
- ``faq_retrieval``: RAG search over a knowledge base.
- ``escalation_gate``: conditional edge routing to either
  ``response_generator`` (high-confidence FAQ / technical answer) or
  ``ticket_creator`` (complaints, low-confidence hits, or missing metadata).
- ``response_generator``: drafts the final customer-facing reply.
- ``ticket_creator``: opens a support ticket and returns an
  acknowledgement.

The builder returns a :class:`SubgraphDefinition` suitable for registration
via :class:`SubgraphRegistry.register`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prismal.agents.subgraphs.customer_service.classifier_node import (
    make_classifier_node,
)
from prismal.agents.subgraphs.customer_service.escalation_node import (
    make_escalation_gate,
)
from prismal.agents.subgraphs.customer_service.faq_retrieval_node import (
    make_faq_retrieval_node,
)
from prismal.agents.subgraphs.customer_service.response_generator_node import (
    make_response_generator_node,
)
from prismal.agents.subgraphs.customer_service.ticket_creator_node import (
    make_ticket_creator_node,
)
from prismal.agents.subgraphs.registry import SubgraphDefinition
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from prismal.core.config import Settings

logger = get_logger("lightagent.subgraphs.customer_service.builder")

_NAME = "customer_service"
_DESCRIPTION = (
    "Customer Service Pipeline: classifier → faq_retrieval → "
    "(escalation_gate) → response_generator | ticket_creator"
)


def build_customer_service_subgraph(
    rag_engine: Any | None = None,
    escalation_threshold: float = 0.6,
    settings: Settings | None = None,
    llm: Any | None = None,
) -> SubgraphDefinition:
    """Build the customer_service :class:`SubgraphDefinition`.

    Args:
        rag_engine: Retrieval engine used by the ``faq_retrieval`` node. When
            ``None``, retrieval short-circuits and the flow always escalates
            (ticket_creator).
        escalation_threshold: Confidence below which the gate escalates to
            ticket creation.
        settings: LightAgent settings. Reserved for future use (e.g. LLM
            provider selection); not required for the in-memory wiring.
        llm: Pre-built LLM handle for classifier + response_generator. When
            ``None``, callers are expected to wire one before registration;
            the factory-returned definition still includes placeholder nodes
            in that case so the shape can be inspected without a live LLM.

    Returns:
        :class:`SubgraphDefinition` with five nodes, linear + conditional
        edges, and ``classifier`` as the entry point.
    """
    del settings  # reserved for future LLM / RAG auto-wiring

    if llm is None:
        from prismal.providers.registry import ProviderRegistry

        llm = ProviderRegistry().get_llm()

    classifier = make_classifier_node(llm=llm)
    faq_retrieval = make_faq_retrieval_node(rag_engine=rag_engine)
    escalation_gate_fn = make_escalation_gate(threshold=escalation_threshold)
    response_generator = make_response_generator_node(llm=llm)
    ticket_creator = make_ticket_creator_node()

    async def _gate_noop(_state: dict[str, Any]) -> dict[str, Any]:
        """Pass-through node; routing happens in the conditional edge."""
        return {}

    definition = SubgraphDefinition(
        name=_NAME,
        description=_DESCRIPTION,
        entry_point="classifier",
        nodes={
            "classifier": classifier,
            "faq_retrieval": faq_retrieval,
            "escalation_gate": _gate_noop,
            "response_generator": response_generator,
            "ticket_creator": ticket_creator,
        },
        edges=[
            ("classifier", "faq_retrieval"),
            ("faq_retrieval", "escalation_gate"),
        ],
        conditional_edges={
            "escalation_gate": escalation_gate_fn,
        },
    )
    logger.info("customer_service_subgraph_built", nodes=list(definition.nodes.keys()))
    return definition


async def register_customer_service(
    rag_engine: Any | None = None,
    escalation_threshold: float = 0.6,
    settings: Settings | None = None,
    llm: Any | None = None,
) -> None:
    """Register the customer_service subgraph in :class:`SubgraphRegistry`.

    Idempotent — skips if already registered. Mirrors the style of
    :func:`register_ml_pipeline` so operators can wire new subgraphs into
    the main graph uniformly.
    """
    from prismal.agents.subgraphs.registry import SubgraphRegistry

    registry = SubgraphRegistry.get_instance()
    if registry.get(_NAME) is not None:
        logger.info("customer_service.already_registered")
        return
    definition = build_customer_service_subgraph(
        rag_engine=rag_engine,
        escalation_threshold=escalation_threshold,
        settings=settings,
        llm=llm,
    )
    await registry.register(_NAME, definition)
    logger.info("customer_service.registered")


__all__ = ["build_customer_service_subgraph", "register_customer_service"]
