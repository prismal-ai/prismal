"""Unit tests for RAGFusionEngine (lightagent.rag.fusion).

RAG-Fusion: generate N query variants, run N searches in parallel, then
re-rank the combined result set via Reciprocal Rank Fusion (RRF).

Tests cover:
- FusionResult dataclass and FusionError hierarchy
- Mathematical correctness of reciprocal_rank_fusion()
- Query-variant generation (LLM parsing)
- Parallel search orchestration
- End-to-end fusion flow

External dependencies (LLM, ChromaVectorStore) are mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lightagent.core.exceptions import FusionError
from lightagent.rag.crag import RetrievedChunk
from lightagent.rag.fusion import (
    FusionResult,
    RAGFusionEngine,
    reciprocal_rank_fusion,
)

PROVIDER_REGISTRY_PATH = "lightagent.rag.fusion.ProviderRegistry"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _chunk(source: str, chunk_id: str, content: str = "", score: float = 0.0) -> RetrievedChunk:
    return RetrievedChunk(
        source=source,
        chunk_id=chunk_id,
        relevance_score=score,
        content=content or f"{source}:{chunk_id}",
    )


def _make_document(content: str, source: str, chunk_id: str) -> MagicMock:
    doc = MagicMock()
    doc.page_content = content
    doc.metadata = {"source": source, "chunk_id": chunk_id}
    return doc


def _make_llm_response(text: str) -> MagicMock:
    response = MagicMock()
    response.content = text
    return response


def _make_settings() -> MagicMock:
    return MagicMock()


# ── Dataclass / exception ─────────────────────────────────────────────────────


def test_fusion_result_is_instantiable() -> None:
    chunk = _chunk("f.txt", "0")
    result = FusionResult(
        chunks=[chunk],
        queries=["q1", "q2"],
        per_query_results={"q1": [chunk], "q2": []},
    )
    assert result.chunks == [chunk]
    assert result.queries == ["q1", "q2"]
    assert result.per_query_results == {"q1": [chunk], "q2": []}


def test_fusion_error_is_ragerror_subclass() -> None:
    from lightagent.core.exceptions import RAGError

    assert issubclass(FusionError, RAGError)


# ── reciprocal_rank_fusion — math ─────────────────────────────────────────────


def test_rrf_empty_lists_returns_empty() -> None:
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[], []]) == []


def test_rrf_single_list_preserves_ordering() -> None:
    chunks = [_chunk("f", "0"), _chunk("f", "1"), _chunk("f", "2")]
    result = reciprocal_rank_fusion([chunks], k=60)
    assert [c.chunk_id for c in result] == ["0", "1", "2"]


def test_rrf_formula_matches_paper() -> None:
    """Verify score = sum over lists of 1 / (k + rank_in_list).

    Using k=60. A appears at rank 1 in list1 and rank 2 in list2:
        score(A) = 1/61 + 1/62 ≈ 0.032523
    B appears at rank 2 in list1 and rank 1 in list2:
        score(B) = 1/62 + 1/61  (same as A — tied)
    C only in list1 at rank 3:
        score(C) = 1/63 ≈ 0.015873
    D only in list2 at rank 3:
        score(D) = 1/63
    """
    a, b, c, d = _chunk("s", "A"), _chunk("s", "B"), _chunk("s", "C"), _chunk("s", "D")
    list1 = [a, b, c]
    list2 = [b, a, d]

    result = reciprocal_rank_fusion([list1, list2], k=60)

    # A and B tie for top; C and D tie for bottom
    top_ids = {result[0].chunk_id, result[1].chunk_id}
    assert top_ids == {"A", "B"}
    bottom_ids = {result[2].chunk_id, result[3].chunk_id}
    assert bottom_ids == {"C", "D"}


def test_rrf_shared_top_wins_over_unique() -> None:
    """A doc at rank 1 in two lists beats a doc at rank 1 in a single list."""
    a = _chunk("s", "shared")
    unique_1 = _chunk("s", "u1")
    unique_2 = _chunk("s", "u2")

    list1 = [a, unique_1]
    list2 = [a, unique_2]

    result = reciprocal_rank_fusion([list1, list2], k=60)
    assert result[0].chunk_id == "shared"


def test_rrf_deduplicates_by_source_and_chunk_id() -> None:
    """The same (source, chunk_id) appearing in multiple lists appears once."""
    a1 = _chunk("s", "X")
    a2 = _chunk("s", "X")  # same identity
    b = _chunk("s", "Y")

    result = reciprocal_rank_fusion([[a1, b], [a2]], k=60)
    ids = [c.chunk_id for c in result]
    assert ids.count("X") == 1
    assert ids.count("Y") == 1


def test_rrf_k_parameter_affects_smoothing() -> None:
    """Larger k flattens the curve; smaller k makes top ranks dominate.

    With k=0, rank 1 contributes 1.0, rank 2 contributes 0.5.
    With k=1000, rank 1 contributes ~1/1001, rank 2 contributes ~1/1002.
    So with large k, the difference between ranks 1 and 2 shrinks.
    """
    a, b = _chunk("s", "A"), _chunk("s", "B")
    # Single list: A rank 1, B rank 2. Both k values preserve order.
    result_small = reciprocal_rank_fusion([[a, b]], k=0)
    result_large = reciprocal_rank_fusion([[a, b]], k=1000)
    assert result_small[0].chunk_id == "A"
    assert result_large[0].chunk_id == "A"


# ── Constructor ───────────────────────────────────────────────────────────────


def test_init_stores_config() -> None:
    store = MagicMock()
    settings = _make_settings()
    with patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls:
        mock_registry_cls.return_value.get_llm.return_value = MagicMock()
        engine = RAGFusionEngine(vector_store=store, n_queries=5, rrf_k=30, settings=settings)
    assert engine._store is store  # noqa: SLF001
    assert engine._n_queries == 5  # noqa: SLF001
    assert engine._rrf_k == 30  # noqa: SLF001
    assert engine._settings is settings  # noqa: SLF001


def test_init_uses_defaults() -> None:
    store = MagicMock()
    with patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls:
        mock_registry_cls.return_value.get_llm.return_value = MagicMock()
        engine = RAGFusionEngine(vector_store=store, settings=_make_settings())
    assert engine._n_queries == 4  # noqa: SLF001
    assert engine._rrf_k == 60  # noqa: SLF001


# ── _generate_query_variants ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_query_variants_parses_numbered_list() -> None:
    """With n_queries=4 → 3 variants (original is added separately by search)."""
    store = MagicMock()
    raw = "1. First variant\n2. Second variant\n3. Third variant"
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=_make_llm_response(raw))
    with patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls:
        mock_registry_cls.return_value.get_llm.return_value = llm
        engine = RAGFusionEngine(vector_store=store, n_queries=4, settings=_make_settings())
        variants = await engine._generate_query_variants("original query")  # noqa: SLF001
    assert len(variants) == 3
    assert variants[0] == "First variant"
    assert variants[-1] == "Third variant"


@pytest.mark.asyncio
async def test_generate_query_variants_returns_empty_when_n_queries_is_1() -> None:
    """n_queries=1 means only the original — no variants to generate."""
    store = MagicMock()
    llm = MagicMock()
    with patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls:
        mock_registry_cls.return_value.get_llm.return_value = llm
        engine = RAGFusionEngine(vector_store=store, n_queries=1, settings=_make_settings())
        variants = await engine._generate_query_variants("q")  # noqa: SLF001
    assert variants == []
    llm.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_generate_query_variants_raises_fusionerror_on_llm_failure() -> None:
    store = MagicMock()
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=RuntimeError("boom"))
    with patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls:
        mock_registry_cls.return_value.get_llm.return_value = llm
        engine = RAGFusionEngine(vector_store=store, settings=_make_settings())
        with pytest.raises(FusionError):
            await engine._generate_query_variants("q")  # noqa: SLF001


# ── search() ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_runs_one_search_per_variant_plus_original() -> None:
    """search() queries with original + N-1 variants, fuses results."""
    store = MagicMock()
    store.similarity_search.return_value = [
        (_make_document("content", "s", "X"), 0.9),
    ]

    llm = MagicMock()
    llm.ainvoke = AsyncMock(
        return_value=_make_llm_response("1. variant 1\n2. variant 2\n3. variant 3")
    )

    with patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls:
        mock_registry_cls.return_value.get_llm.return_value = llm
        engine = RAGFusionEngine(vector_store=store, n_queries=4, settings=_make_settings())
        result = await engine.search("original", k=5)

    # 4 total queries = original + 3 variants
    assert store.similarity_search.call_count == 4
    assert isinstance(result, FusionResult)
    # Original query must be in the list of queries
    assert "original" in result.queries
    # per_query_results has one entry per query
    assert len(result.per_query_results) == 4


@pytest.mark.asyncio
async def test_search_returns_fused_chunks() -> None:
    """Result.chunks must be the RRF-fused union."""
    docA = _make_document("a", "s", "A")
    docB = _make_document("b", "s", "B")
    # All 4 queries return the same single doc → it must appear exactly once.
    store = MagicMock()
    store.similarity_search.side_effect = [
        [(docA, 0.9), (docB, 0.8)],
        [(docA, 0.9), (docB, 0.8)],
        [(docA, 0.9), (docB, 0.8)],
        [(docA, 0.9), (docB, 0.8)],
    ]
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=_make_llm_response("1. v1\n2. v2\n3. v3"))
    with patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls:
        mock_registry_cls.return_value.get_llm.return_value = llm
        engine = RAGFusionEngine(vector_store=store, n_queries=4, settings=_make_settings())
        result = await engine.search("original", k=5)

    ids = [c.chunk_id for c in result.chunks]
    assert ids.count("A") == 1
    assert ids.count("B") == 1
    # A appears before B (all lists agree on rank)
    assert ids.index("A") < ids.index("B")


@pytest.mark.asyncio
async def test_search_raises_fusionerror_when_all_variants_fail() -> None:
    """If variant generation fails, search() surfaces FusionError."""
    store = MagicMock()
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=RuntimeError("boom"))
    with patch(PROVIDER_REGISTRY_PATH) as mock_registry_cls:
        mock_registry_cls.return_value.get_llm.return_value = llm
        engine = RAGFusionEngine(vector_store=store, settings=_make_settings())
        with pytest.raises(FusionError):
            await engine.search("q", k=5)
