"""Unit tests for customer_service subgraph nodes and builder.

Pipeline (SPEC-SUBGRAPH-001)::

    classifier → faq_retrieval → [escalation gate] ─┬→ response_generator
                                                     └→ ticket_creator

- classifier: LLM classifies a query as FAQ / Complaint / Technical / Other.
- faq_retrieval: RAG search; writes retrieved chunks + confidence to state.
- escalation gate: if confidence < threshold OR category=="Complaint" →
  ticket_creator; else → response_generator.
- response_generator: LLM composes the answer using retrieved context.
- ticket_creator: assigns a ticket id and records it on the state.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from lightagent.agents.subgraphs.customer_service import (
    CUSTOMER_SERVICE_CATEGORIES,
    CustomerServiceError,
    make_classifier_node,
    make_escalation_gate,
    make_faq_retrieval_node,
    make_response_generator_node,
    make_ticket_creator_node,
)
from lightagent.agents.subgraphs.customer_service.builder import (
    build_customer_service_subgraph,
)
from lightagent.core.exceptions import LightAgentError

# ── Helpers ───────────────────────────────────────────────────────────────────


def _state(query: str = "What are your shipping options?", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "messages": [HumanMessage(content=query)],
        "metadata": {},
    }
    base.update(overrides)
    return base


def _llm_returning(text: str) -> MagicMock:
    llm = MagicMock()
    response = MagicMock()
    response.content = text
    llm.ainvoke = AsyncMock(return_value=response)
    return llm


# ── Categories / exception ────────────────────────────────────────────────────


def test_customer_service_categories_exposed() -> None:
    assert set(CUSTOMER_SERVICE_CATEGORIES) == {
        "faq",
        "complaint",
        "technical",
        "other",
    }


def test_customer_service_error_inherits_from_lightagent_error() -> None:
    assert issubclass(CustomerServiceError, LightAgentError)


# ── classifier_node ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classifier_node_parses_faq_token() -> None:
    llm = _llm_returning("faq")
    node = make_classifier_node(llm=llm)
    update = await node(_state("What are your return policies?"))
    cs = update["metadata"]["customer_service"]
    assert cs["category"] == "faq"


@pytest.mark.asyncio
async def test_classifier_node_parses_complaint_token() -> None:
    llm = _llm_returning("COMPLAINT: user is angry about late delivery")
    node = make_classifier_node(llm=llm)
    update = await node(_state("The package never arrived!"))
    assert update["metadata"]["customer_service"]["category"] == "complaint"


@pytest.mark.asyncio
async def test_classifier_node_defaults_to_other_when_unparseable() -> None:
    llm = _llm_returning("I'm not sure")
    node = make_classifier_node(llm=llm)
    update = await node(_state("???"))
    assert update["metadata"]["customer_service"]["category"] == "other"


@pytest.mark.asyncio
async def test_classifier_node_returns_empty_on_empty_messages() -> None:
    llm = _llm_returning("faq")
    node = make_classifier_node(llm=llm)
    update = await node({"messages": [], "metadata": {}})
    assert update == {}
    llm.ainvoke.assert_not_called()


# ── faq_retrieval_node ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_faq_retrieval_node_writes_chunks_and_confidence() -> None:
    rag_engine = MagicMock()
    chunks = [
        MagicMock(content="FAQ answer part 1", relevance_score=0.92, source="faq.md"),
        MagicMock(content="FAQ answer part 2", relevance_score=0.80, source="faq.md"),
    ]
    rag_engine.search = MagicMock(return_value=chunks)
    node = make_faq_retrieval_node(rag_engine=rag_engine)

    state = _state("How do I reset my password?")
    state["metadata"]["customer_service"] = {"category": "faq"}
    update = await node(state)

    cs = update["metadata"]["customer_service"]
    assert len(cs["retrieved"]) == 2
    # Confidence = best chunk relevance score
    assert cs["confidence"] == pytest.approx(0.92)


@pytest.mark.asyncio
async def test_faq_retrieval_node_confidence_zero_when_no_results() -> None:
    rag_engine = MagicMock()
    rag_engine.search = MagicMock(return_value=[])
    node = make_faq_retrieval_node(rag_engine=rag_engine)

    state = _state("Esoteric question")
    state["metadata"]["customer_service"] = {"category": "faq"}
    update = await node(state)

    cs = update["metadata"]["customer_service"]
    assert cs["retrieved"] == []
    assert cs["confidence"] == 0.0


@pytest.mark.asyncio
async def test_faq_retrieval_node_no_rag_engine_short_circuits() -> None:
    """If rag_engine=None the node records empty retrieval with confidence 0.0."""
    node = make_faq_retrieval_node(rag_engine=None)

    state = _state("Q")
    state["metadata"]["customer_service"] = {"category": "faq"}
    update = await node(state)

    assert update["metadata"]["customer_service"]["confidence"] == 0.0


# ── escalation gate ──────────────────────────────────────────────────────────


def test_escalation_gate_routes_complaint_to_ticket_creator() -> None:
    gate = make_escalation_gate(threshold=0.6)
    state = _state()
    state["metadata"]["customer_service"] = {"category": "complaint", "confidence": 0.99}
    assert gate(state) == "ticket_creator"


def test_escalation_gate_routes_low_confidence_to_ticket_creator() -> None:
    gate = make_escalation_gate(threshold=0.6)
    state = _state()
    state["metadata"]["customer_service"] = {"category": "faq", "confidence": 0.3}
    assert gate(state) == "ticket_creator"


def test_escalation_gate_routes_confident_faq_to_response_generator() -> None:
    gate = make_escalation_gate(threshold=0.6)
    state = _state()
    state["metadata"]["customer_service"] = {"category": "faq", "confidence": 0.9}
    assert gate(state) == "response_generator"


def test_escalation_gate_routes_technical_confident_to_response_generator() -> None:
    gate = make_escalation_gate(threshold=0.6)
    state = _state()
    state["metadata"]["customer_service"] = {"category": "technical", "confidence": 0.8}
    assert gate(state) == "response_generator"


def test_escalation_gate_defaults_to_ticket_creator_when_metadata_missing() -> None:
    """Defensive: if category/confidence aren't set, escalate (safer choice)."""
    gate = make_escalation_gate(threshold=0.6)
    state = _state()
    assert gate(state) == "ticket_creator"


# ── response_generator_node ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_response_generator_composes_answer_with_context() -> None:
    llm = _llm_returning("Per our policy, returns are accepted within 30 days.")
    node = make_response_generator_node(llm=llm)
    state = _state("What is your return policy?")
    state["metadata"]["customer_service"] = {
        "category": "faq",
        "confidence": 0.9,
        "retrieved": [
            MagicMock(content="Returns accepted within 30 days.", source="policy.md")
        ],
    }
    update = await node(state)

    msgs = update["messages"]
    assert len(msgs) == 1
    assert isinstance(msgs[0], AIMessage)
    assert "30 days" in msgs[0].content


@pytest.mark.asyncio
async def test_response_generator_handles_missing_retrieval_gracefully() -> None:
    llm = _llm_returning("I'd need more information to answer that.")
    node = make_response_generator_node(llm=llm)
    state = _state("q")
    state["metadata"]["customer_service"] = {
        "category": "faq",
        "confidence": 0.0,
        "retrieved": [],
    }
    update = await node(state)
    assert "messages" in update


# ── ticket_creator_node ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ticket_creator_generates_ticket_id_and_records_in_state() -> None:
    node = make_ticket_creator_node()
    state = _state("The product is broken!")
    state["metadata"]["customer_service"] = {"category": "complaint", "confidence": 0.2}
    update = await node(state)

    ticket_id = update["metadata"]["customer_service"]["ticket_id"]
    assert ticket_id.startswith("TK-")
    # An AIMessage announcing the ticket was added
    msgs = update["messages"]
    assert len(msgs) == 1
    assert ticket_id in msgs[0].content


@pytest.mark.asyncio
async def test_ticket_creator_stores_reason_from_metadata() -> None:
    node = make_ticket_creator_node()
    state = _state("I got charged twice!")
    state["metadata"]["customer_service"] = {
        "category": "complaint",
        "confidence": 0.4,
    }
    update = await node(state)
    cs = update["metadata"]["customer_service"]
    assert "reason" in cs
    assert cs["reason"]  # non-empty


# ── builder ──────────────────────────────────────────────────────────────────


def test_build_customer_service_subgraph_returns_definition_with_five_nodes() -> None:
    defn = build_customer_service_subgraph(
        rag_engine=MagicMock(),
        llm=_llm_returning("x"),
        escalation_threshold=0.6,
    )
    node_names = set(defn.nodes.keys())
    assert node_names == {
        "classifier",
        "faq_retrieval",
        "escalation_gate",
        "response_generator",
        "ticket_creator",
    }


def test_build_customer_service_subgraph_entry_point_is_classifier() -> None:
    defn = build_customer_service_subgraph(
        rag_engine=MagicMock(),
        llm=_llm_returning("x"),
    )
    assert defn.entry_point == "classifier"


def test_build_customer_service_subgraph_has_conditional_gate_on_escalation() -> None:
    """The escalation_gate must be wired as a conditional edge with 2 targets."""
    defn = build_customer_service_subgraph(
        rag_engine=MagicMock(),
        llm=_llm_returning("x"),
        escalation_threshold=0.6,
    )
    assert "escalation_gate" in defn.conditional_edges


def test_build_customer_service_subgraph_linear_edges_are_declared() -> None:
    defn = build_customer_service_subgraph(
        rag_engine=MagicMock(),
        llm=_llm_returning("x"),
    )
    edges = set(defn.edges)
    # classifier → faq_retrieval → escalation_gate are linear; the conditional
    # routing sits on escalation_gate.
    assert ("classifier", "faq_retrieval") in edges
    assert ("faq_retrieval", "escalation_gate") in edges
