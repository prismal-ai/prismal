"""Integration test — AdaptiveRAGEngine + ConstitutionalFilter (D1-06).

End-to-end scenario: a user query is classified and routed by
AdaptiveRAGEngine to the best RAG engine; the retrieved answer is then
filtered through ConstitutionalFilter to check / revise for principle
violations. Everything is mocked so the test runs without any real LLM.

This test exercises the shape of how the two new subsystems compose,
validating that their state types interoperate.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prismal.agents.patterns.constitutional import (
    ConstitutionalFilter,
    ConstitutionalPrinciple,
)
from prismal.rag.adaptive import AdaptiveRAGEngine, QueryType
from prismal.rag.crag import CRAGResult, RetrievedChunk


def _response(text: str) -> MagicMock:
    r = MagicMock()
    r.content = text
    return r


@pytest.mark.integration
@pytest.mark.asyncio
async def test_adaptive_rag_then_constitutional_filter() -> None:
    """FACTUAL_SIMPLE query → CRAG → ConstitutionalFilter passes clean output."""
    chunk = RetrievedChunk(
        source="doc.md",
        chunk_id="0",
        relevance_score=0.92,
        content="LangGraph was announced in 2024.",
    )
    crag = MagicMock()
    crag.run = AsyncMock(
        return_value=CRAGResult(
            answer="LangGraph was announced in 2024.",
            sources=[chunk],
            used_web_fallback=False,
        )
    )

    # Adaptive RAG classifies the query, routes to CRAG.
    adaptive = AdaptiveRAGEngine(crag_engine=crag, settings=MagicMock())
    result = await adaptive.search("When was LangGraph released?", k=3)
    assert result.query_type is QueryType.FACTUAL_SIMPLE
    assert result.strategy_used == "crag"
    assert result.chunks == [chunk]

    # Pipeline the answer through Constitutional AI with a benign principle.
    principle = ConstitutionalPrinciple(
        id="P_T",
        name="test",
        description="benign",
        critique_prompt="Reply OK or VIOLATION.",
        revision_prompt="Rewrite.",
    )
    # The LLM always returns "OK" → no violation → no revision.
    filter_llm = MagicMock()
    filter_llm.ainvoke = AsyncMock(return_value=_response("OK: clean"))
    with patch("lightagent.agents.patterns.constitutional.ProviderRegistry") as reg:
        reg.return_value.get_llm.return_value = filter_llm
        filt = ConstitutionalFilter(
            principles=[principle],
            settings=MagicMock(),
        )
        const_result = await filt.apply(
            output=result.chunks[0].content,
            context="When was LangGraph released?",
        )

    # End-to-end: both subsystems succeeded and the output is the same.
    assert const_result.final_output == chunk.content
    assert const_result.all_principles_satisfied is True
    assert const_result.revisions == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_adaptive_rag_plus_constitutional_revision_flow() -> None:
    """When the RAG output violates a principle, Constitutional revises it."""
    chunk = RetrievedChunk(
        source="doc.md",
        chunk_id="0",
        relevance_score=0.9,
        content="Violating content that must be rewritten.",
    )
    crag = MagicMock()
    crag.run = AsyncMock(
        return_value=CRAGResult(
            answer="Violating content that must be rewritten.",
            sources=[chunk],
            used_web_fallback=False,
        )
    )
    adaptive = AdaptiveRAGEngine(crag_engine=crag, settings=MagicMock())
    result = await adaptive.search("What did X do?", k=3)

    principle = ConstitutionalPrinciple(
        id="P_T",
        name="test",
        description="demo",
        critique_prompt="Reply OK or VIOLATION.",
        revision_prompt="Rewrite cleanly.",
    )
    filter_llm = MagicMock()
    filter_llm.ainvoke = AsyncMock(
        side_effect=[
            _response("VIOLATION: problematic"),
            _response("Revised clean content."),
            _response("OK: now clean"),
        ]
    )
    with patch("lightagent.agents.patterns.constitutional.ProviderRegistry") as reg:
        reg.return_value.get_llm.return_value = filter_llm
        filt = ConstitutionalFilter(principles=[principle], settings=MagicMock())
        const_result = await filt.apply(result.chunks[0].content)

    assert const_result.final_output == "Revised clean content."
    assert len(const_result.revisions) == 1
    assert const_result.all_principles_satisfied is True
