"""Unit tests for HybridSearchEngine (prismal.rag.hybrid).

Hybrid search combines BM25 lexical scoring with embedding semantic scoring
via linear fusion: ``score = alpha * sem + (1-alpha) * bm25``.

Tests cover:
- Dataclass / exception hierarchy
- alpha=0 → BM25 only; alpha=1 → semantic only
- BM25 finds exact-term matches that embeddings miss
- build_index input validation
- search() deduplicates overlapping results
- alpha per-call override works

The semantic side is mocked via a fake ChromaVectorStore; the BM25 side runs
on a real small corpus.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from prismal.core.exceptions import HybridSearchError
from prismal.rag.hybrid import HybridSearchEngine

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_document(content: str, source: str, chunk_id: str) -> MagicMock:
    doc = MagicMock()
    doc.page_content = content
    doc.metadata = {"source": source, "chunk_id": chunk_id}
    return doc


def _fake_store(results: list[tuple[MagicMock, float]] | None = None) -> MagicMock:
    store = MagicMock()
    store.similarity_search.return_value = results or []
    return store


# ── Exception & init ──────────────────────────────────────────────────────────


def test_hybrid_search_error_is_ragerror_subclass() -> None:
    from prismal.core.exceptions import RAGError

    assert issubclass(HybridSearchError, RAGError)


def test_init_stores_config() -> None:
    store = _fake_store()
    engine = HybridSearchEngine(vector_store=store, alpha=0.7, settings=MagicMock())
    assert engine._store is store  # noqa: SLF001
    assert engine._alpha == 0.7  # noqa: SLF001


def test_init_rejects_alpha_out_of_range() -> None:
    store = _fake_store()
    with pytest.raises(ValueError):
        HybridSearchEngine(vector_store=store, alpha=1.5, settings=MagicMock())
    with pytest.raises(ValueError):
        HybridSearchEngine(vector_store=store, alpha=-0.1, settings=MagicMock())


# ── build_index ───────────────────────────────────────────────────────────────


def test_build_index_rejects_length_mismatch() -> None:
    engine = HybridSearchEngine(vector_store=_fake_store(), settings=MagicMock())
    with pytest.raises(ValueError):
        engine.build_index(corpus=["a", "b"], doc_ids=["1"])


def test_build_index_accepts_matching_lengths() -> None:
    engine = HybridSearchEngine(vector_store=_fake_store(), settings=MagicMock())
    engine.build_index(
        corpus=["the cat sat on the mat", "the dog barked"],
        doc_ids=["doc1:0", "doc2:0"],
    )
    assert engine.has_index()


def test_search_without_index_falls_back_to_semantic() -> None:
    """With no BM25 index, alpha should be effectively 1.0 (semantic only)."""
    doc = _make_document("content", "s1.txt", "0")
    store = _fake_store([(doc, 0.9)])
    engine = HybridSearchEngine(vector_store=store, alpha=0.5, settings=MagicMock())
    results = engine.search("query", k=5)
    assert len(results) == 1
    assert results[0].source == "s1.txt"


# ── alpha endpoints ───────────────────────────────────────────────────────────


def _filler_corpus() -> tuple[list[str], list[str]]:
    """Return a >4-doc filler corpus so BM25 IDF can discriminate.

    BM25Okapi's IDF returns 0 when a term appears in ~half of documents; tests
    need enough non-matching filler docs to drive meaningful scores.
    """
    texts = [
        "alpha beta gamma",
        "delta epsilon zeta",
        "eta theta iota",
        "kappa lambda mu",
        "nu xi omicron",
    ]
    ids = [f"filler{i}.txt:0" for i in range(len(texts))]
    return texts, ids


def test_alpha_zero_uses_only_bm25() -> None:
    """alpha=0 means 100% BM25; the doc with exact-term match should win."""
    doc1 = _make_document("LangGraph supervisor pattern tutorial", "doc1.txt", "0")
    doc2 = _make_document("unrelated content about cats", "doc2.txt", "0")
    # Embeddings return doc2 as best (semantic match) to ensure BM25 wins
    store = _fake_store([(doc2, 0.95), (doc1, 0.20)])

    filler_corpus, filler_ids = _filler_corpus()
    engine = HybridSearchEngine(vector_store=store, alpha=0.0, settings=MagicMock())
    engine.build_index(
        corpus=[
            "LangGraph supervisor pattern tutorial",
            "unrelated content about cats",
            *filler_corpus,
        ],
        doc_ids=["doc1.txt:0", "doc2.txt:0", *filler_ids],
    )
    results = engine.search("LangGraph supervisor", k=5)
    assert results[0].source == "doc1.txt"  # BM25 finds exact terms


def test_alpha_one_uses_only_semantic() -> None:
    """alpha=1 means 100% semantic; the vector store's top result wins."""
    doc1 = _make_document("completely irrelevant lexically", "doc1.txt", "0")
    doc2 = _make_document("LangGraph LangGraph LangGraph", "doc2.txt", "0")
    # Semantic store returns doc1 as best despite doc2 having the lexical match
    store = _fake_store([(doc1, 0.99), (doc2, 0.05)])

    filler_corpus, filler_ids = _filler_corpus()
    engine = HybridSearchEngine(vector_store=store, alpha=1.0, settings=MagicMock())
    engine.build_index(
        corpus=[
            "completely irrelevant lexically",
            "LangGraph LangGraph LangGraph",
            *filler_corpus,
        ],
        doc_ids=["doc1.txt:0", "doc2.txt:0", *filler_ids],
    )
    results = engine.search("LangGraph", k=5)
    assert results[0].source == "doc1.txt"  # semantic wins


def test_search_respects_per_call_alpha_override() -> None:
    """alpha passed to search() overrides the constructor alpha."""
    doc_bm25_winner = _make_document("exact term match rare", "bm25.txt", "0")
    doc_semantic_winner = _make_document("different content", "sem.txt", "0")
    # Semantic ranks doc_semantic_winner first
    store = _fake_store([(doc_semantic_winner, 0.95), (doc_bm25_winner, 0.10)])

    filler_corpus, filler_ids = _filler_corpus()
    engine = HybridSearchEngine(vector_store=store, alpha=1.0, settings=MagicMock())
    engine.build_index(
        corpus=[
            "exact term match rare",
            "different content",
            *filler_corpus,
        ],
        doc_ids=["bm25.txt:0", "sem.txt:0", *filler_ids],
    )
    # Override to pure BM25 for this call
    results = engine.search("exact term match rare", k=5, alpha=0.0)
    assert results[0].source == "bm25.txt"


# ── search() behavior ─────────────────────────────────────────────────────────


def test_search_deduplicates_overlap_between_bm25_and_semantic() -> None:
    """A doc present in both result sets must appear exactly once."""
    doc = _make_document("supervisor pattern", "shared.txt", "0")
    other = _make_document("other content", "other.txt", "0")
    store = _fake_store([(doc, 0.9), (other, 0.6)])

    engine = HybridSearchEngine(vector_store=store, alpha=0.5, settings=MagicMock())
    engine.build_index(
        corpus=["supervisor pattern", "other content"],
        doc_ids=["shared.txt:0", "other.txt:0"],
    )
    results = engine.search("supervisor", k=5)
    sources = [r.source for r in results]
    assert sources.count("shared.txt") == 1


def test_search_returns_at_most_k_results() -> None:
    docs = [_make_document(f"doc{i}", f"d{i}.txt", "0") for i in range(10)]
    store = _fake_store([(d, 0.5) for d in docs])
    engine = HybridSearchEngine(vector_store=store, alpha=0.5, settings=MagicMock())
    engine.build_index(
        corpus=[f"doc{i}" for i in range(10)],
        doc_ids=[f"d{i}.txt:0" for i in range(10)],
    )
    results = engine.search("doc", k=3)
    assert len(results) <= 3


def test_search_invalid_alpha_raises() -> None:
    engine = HybridSearchEngine(vector_store=_fake_store(), settings=MagicMock())
    with pytest.raises(ValueError):
        engine.search("q", k=5, alpha=2.0)
