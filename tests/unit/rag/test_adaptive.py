"""Unit tests for AdaptiveRAGEngine (prismal.rag.adaptive).

Adaptive RAG classifies the query and routes it to the best-suited engine
from Fase A (CRAG / HyDE / Fusion / Hybrid / Hierarchical). Falls back to
CRAG when the preferred engine is not configured.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prismal.core.exceptions import AdaptiveRAGError, RAGError
from prismal.rag.adaptive import AdaptiveRAGEngine, AdaptiveResult, QueryType
from prismal.rag.crag import CRAGResult, RetrievedChunk
from prismal.rag.fusion import FusionResult
from prismal.rag.hyde import HyDEResult


def _chunk(source: str = "f.txt", cid: str = "0", content: str = "c") -> RetrievedChunk:
    return RetrievedChunk(source=source, chunk_id=cid, relevance_score=0.9, content=content)


def _mock_crag(chunks: list[RetrievedChunk]) -> MagicMock:
    c = MagicMock()
    c.run = AsyncMock(return_value=CRAGResult(answer="a", sources=chunks, used_web_fallback=False))
    return c


def _mock_hyde(chunks: list[RetrievedChunk]) -> MagicMock:
    h = MagicMock()
    h.search = AsyncMock(
        return_value=HyDEResult(chunks=chunks, hypothesis="h", hypothesis_embedding=[0.0])
    )
    return h


def _mock_fusion(chunks: list[RetrievedChunk]) -> MagicMock:
    f = MagicMock()
    f.search = AsyncMock(
        return_value=FusionResult(chunks=chunks, queries=["q"], per_query_results={})
    )
    return f


def _mock_hybrid(chunks: list[RetrievedChunk]) -> MagicMock:
    hy = MagicMock()
    hy.search = MagicMock(return_value=chunks)  # sync
    return hy


# ── Enum + dataclass + exception ─────────────────────────────────────────────


def test_query_type_values() -> None:
    assert QueryType.FACTUAL_SIMPLE.value == "factual_simple"
    assert QueryType.ABSTRACT.value == "abstract"
    assert QueryType.AMBIGUOUS.value == "ambiguous"
    assert QueryType.MULTI_HOP.value == "multi_hop"
    assert QueryType.TECHNICAL.value == "technical"
    assert QueryType.CONVERSATIONAL.value == "conversational"


def test_adaptive_result_is_instantiable() -> None:
    r = AdaptiveResult(
        chunks=[_chunk()],
        strategy_used="crag",
        query_type=QueryType.FACTUAL_SIMPLE,
        confidence=0.8,
    )
    assert r.strategy_used == "crag"
    assert r.query_type is QueryType.FACTUAL_SIMPLE
    assert r.confidence == 0.8


def test_adaptive_rag_error_is_ragerror_subclass() -> None:
    assert issubclass(AdaptiveRAGError, RAGError)


# ── classify_query (regex heuristics) ────────────────────────────────────────


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        # FACTUAL_SIMPLE: wh-question about a specific fact
        ("When was LangGraph released?", QueryType.FACTUAL_SIMPLE),
        ("Who wrote the paper?", QueryType.FACTUAL_SIMPLE),
        ("How many agents does the supervisor route to?", QueryType.FACTUAL_SIMPLE),
        # ABSTRACT: why-question
        ("Why does LangGraph use StateGraph?", QueryType.ABSTRACT),
        ("Por qué se eligió supervisor?", QueryType.ABSTRACT),
        # CONVERSATIONAL: references prior discussion
        ("What did we discuss earlier about the graph?", QueryType.CONVERSATIONAL),
        ("Can you expand on the previous point?", QueryType.CONVERSATIONAL),
        # MULTI_HOP: composite phrasing
        ("First compile the graph, then execute it step by step", QueryType.MULTI_HOP),
        # AMBIGUOUS: very short / vague
        ("stuff", QueryType.AMBIGUOUS),
        ("tell me something", QueryType.AMBIGUOUS),
    ],
)
def test_classify_query_heuristics(query: str, expected: QueryType) -> None:
    engine = AdaptiveRAGEngine(crag_engine=_mock_crag([]), settings=MagicMock())
    qt, confidence = engine.classify_query(query)
    assert qt is expected
    assert 0.0 <= confidence <= 1.0


def test_classify_query_returns_technical_for_code_tokens() -> None:
    """Queries containing code-like tokens (snake_case, API, SDK) are TECHNICAL."""
    engine = AdaptiveRAGEngine(crag_engine=_mock_crag([]), settings=MagicMock())
    for q in ["How to use the snake_case variable", "What is the API for SDK?"]:
        qt, _ = engine.classify_query(q)
        assert qt is QueryType.TECHNICAL


# ── search() routing ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_routes_abstract_to_hyde_when_available() -> None:
    chunks = [_chunk("hyde.txt")]
    hyde = _mock_hyde(chunks)
    engine = AdaptiveRAGEngine(
        crag_engine=_mock_crag([]),
        hyde_retriever=hyde,
        settings=MagicMock(),
    )
    result = await engine.search("Why does the supervisor exist?", k=3)
    hyde.search.assert_awaited_once()
    assert result.strategy_used == "hyde"
    assert result.query_type is QueryType.ABSTRACT
    assert result.chunks == chunks


@pytest.mark.asyncio
async def test_search_routes_ambiguous_to_fusion_when_available() -> None:
    chunks = [_chunk("fusion.txt")]
    fusion = _mock_fusion(chunks)
    engine = AdaptiveRAGEngine(
        crag_engine=_mock_crag([]),
        fusion_engine=fusion,
        settings=MagicMock(),
    )
    result = await engine.search("stuff", k=3)
    fusion.search.assert_awaited_once()
    assert result.strategy_used == "fusion"
    assert result.query_type is QueryType.AMBIGUOUS


@pytest.mark.asyncio
async def test_search_routes_technical_to_hybrid_when_available() -> None:
    chunks = [_chunk("hybrid.txt")]
    hybrid = _mock_hybrid(chunks)
    engine = AdaptiveRAGEngine(
        crag_engine=_mock_crag([]),
        hybrid_engine=hybrid,
        settings=MagicMock(),
    )
    result = await engine.search("What is the API for SDK?", k=3)
    hybrid.search.assert_called_once()
    assert result.strategy_used == "hybrid"
    assert result.query_type is QueryType.TECHNICAL


@pytest.mark.asyncio
async def test_search_falls_back_to_crag_when_preferred_engine_missing() -> None:
    """ABSTRACT query but no hyde_retriever → CRAG."""
    chunks = [_chunk("crag.txt")]
    crag = _mock_crag(chunks)
    engine = AdaptiveRAGEngine(crag_engine=crag, settings=MagicMock())
    result = await engine.search("Why does the supervisor exist?", k=3)
    crag.run.assert_awaited_once()
    assert result.strategy_used == "crag"
    assert result.query_type is QueryType.ABSTRACT  # classification unchanged
    assert result.chunks == chunks


@pytest.mark.asyncio
async def test_search_factual_simple_uses_crag_by_default() -> None:
    chunks = [_chunk("crag.txt")]
    crag = _mock_crag(chunks)
    engine = AdaptiveRAGEngine(crag_engine=crag, settings=MagicMock())
    result = await engine.search("When was LangGraph released?", k=3)
    crag.run.assert_awaited_once()
    assert result.strategy_used == "crag"


# ── force_strategy override ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_force_strategy_overrides_classification() -> None:
    hyde = _mock_hyde([_chunk()])
    crag = _mock_crag([])
    engine = AdaptiveRAGEngine(crag_engine=crag, hyde_retriever=hyde, settings=MagicMock())
    # Classifier would say FACTUAL_SIMPLE → CRAG; force hyde.
    await engine.search("When was LangGraph released?", k=3, force_strategy="hyde")
    hyde.search.assert_awaited_once()
    crag.run.assert_not_called()


@pytest.mark.asyncio
async def test_force_strategy_unknown_raises() -> None:
    engine = AdaptiveRAGEngine(crag_engine=_mock_crag([]), settings=MagicMock())
    with pytest.raises(ValueError):
        await engine.search("q", force_strategy="nonexistent-engine")


@pytest.mark.asyncio
async def test_force_strategy_requires_engine_available() -> None:
    """Forcing hyde without hyde_retriever configured raises."""
    engine = AdaptiveRAGEngine(crag_engine=_mock_crag([]), settings=MagicMock())
    with pytest.raises(AdaptiveRAGError):
        await engine.search("q", force_strategy="hyde")


# ── LLM classifier path ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_with_llm_classifier_uses_llm(monkeypatch) -> None:
    """When use_llm_classifier=True, the LLM is consulted to classify."""
    crag = _mock_crag([])
    # Patch the LLM call site in adaptive.py
    with patch("prismal.rag.adaptive.ProviderRegistry") as reg:
        llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "technical"
        llm.ainvoke = AsyncMock(return_value=mock_response)
        reg.return_value.get_llm.return_value = llm

        hybrid = _mock_hybrid([_chunk()])
        engine = AdaptiveRAGEngine(
            crag_engine=crag,
            hybrid_engine=hybrid,
            use_llm_classifier=True,
            settings=MagicMock(),
        )
        result = await engine.search("some query", k=3)

    llm.ainvoke.assert_awaited_once()
    assert result.query_type is QueryType.TECHNICAL
    assert result.strategy_used == "hybrid"


@pytest.mark.asyncio
async def test_llm_classifier_falls_back_to_regex_on_error() -> None:
    crag = _mock_crag([])
    with patch("prismal.rag.adaptive.ProviderRegistry") as reg:
        llm = MagicMock()
        llm.ainvoke = AsyncMock(side_effect=RuntimeError("boom"))
        reg.return_value.get_llm.return_value = llm
        engine = AdaptiveRAGEngine(
            crag_engine=crag,
            use_llm_classifier=True,
            settings=MagicMock(),
        )
        result = await engine.search("When was it released?", k=3)
    # Regex fallback → FACTUAL_SIMPLE
    assert result.query_type is QueryType.FACTUAL_SIMPLE
