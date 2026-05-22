"""Unit tests for HierarchicalRAGEngine (lightagent.rag.hierarchical).

Parent-Child RAG indexes small child chunks for high retrieval precision,
but returns the enclosing parent chunk for broader generation context.

Tests cover:
- Dataclasses (ParentChunk, HierarchicalSearchResult) + error hierarchy
- Constructor validation
- index_document loads document, splits into parent/child chunks, writes
  parent metadata to Chroma, and calls delete_by_source for dedup
- search() groups child hits by parent_id and reconstructs ParentChunk
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from prismal.core.exceptions import HierarchicalRAGError, RAGError
from prismal.rag.hierarchical import (
    HierarchicalRAGEngine,
    HierarchicalSearchResult,
    ParentChunk,
)

LOADER_PATH = "lightagent.rag.hierarchical.DocumentProcessorFactory"


def _make_document(content: str, source: str = "file.txt") -> MagicMock:
    doc = MagicMock()
    doc.page_content = content
    doc.metadata = {"source": source}
    return doc


def _child_result(content: str, source: str, chunk_id: str, parent_id: str, parent_content: str):
    doc = MagicMock()
    doc.page_content = content
    doc.metadata = {
        "source": source,
        "chunk_id": chunk_id,
        "parent_id": parent_id,
        "parent_content": parent_content,
    }
    return doc


# ── Dataclasses + exception ──────────────────────────────────────────────────


def test_parent_chunk_is_instantiable() -> None:
    p = ParentChunk(
        parent_id="p0",
        source="f.txt",
        content="big parent block",
        child_ids=["c0", "c1"],
    )
    assert p.parent_id == "p0"
    assert p.source == "f.txt"
    assert p.content == "big parent block"
    assert p.child_ids == ["c0", "c1"]


def test_hierarchical_search_result_is_instantiable() -> None:
    p = ParentChunk(parent_id="p0", source="f.txt", content="x", child_ids=["c0"])
    result = HierarchicalSearchResult(parent_chunks=[p], matched_child_ids=["c0"])
    assert result.parent_chunks == [p]
    assert result.matched_child_ids == ["c0"]


def test_hierarchical_rag_error_is_ragerror_subclass() -> None:
    assert issubclass(HierarchicalRAGError, RAGError)


# ── Constructor ──────────────────────────────────────────────────────────────


def test_init_stores_config() -> None:
    store = MagicMock()
    engine = HierarchicalRAGEngine(
        vector_store=store,
        parent_chunk_size=500,
        child_chunk_size=100,
        child_overlap=20,
        settings=MagicMock(),
    )
    assert engine._store is store  # noqa: SLF001
    assert engine._parent_size == 500  # noqa: SLF001
    assert engine._child_size == 100  # noqa: SLF001
    assert engine._child_overlap == 20  # noqa: SLF001


def test_init_rejects_child_larger_than_parent() -> None:
    with pytest.raises(ValueError):
        HierarchicalRAGEngine(
            vector_store=MagicMock(),
            parent_chunk_size=100,
            child_chunk_size=200,
            settings=MagicMock(),
        )


def test_init_rejects_overlap_gte_child_size() -> None:
    with pytest.raises(ValueError):
        HierarchicalRAGEngine(
            vector_store=MagicMock(),
            parent_chunk_size=500,
            child_chunk_size=100,
            child_overlap=150,
            settings=MagicMock(),
        )


# ── index_document ───────────────────────────────────────────────────────────


def test_index_document_calls_delete_by_source_first() -> None:
    """AC-005-7 compatibility: re-indexing must not duplicate."""
    doc = _make_document("some content " * 100, "file.txt")
    store = MagicMock()
    with patch(LOADER_PATH) as mock_factory_cls:
        mock_factory_cls.return_value.load.return_value = [doc]
        engine = HierarchicalRAGEngine(
            vector_store=store,
            parent_chunk_size=100,
            child_chunk_size=30,
            child_overlap=5,
            settings=MagicMock(),
        )
        engine.index_document(Path("file.txt"))

    store.delete_by_source.assert_called_once_with("file.txt")


def test_index_document_splits_into_parent_and_child_chunks() -> None:
    content = "A" * 500  # 500 chars
    doc = _make_document(content, "file.txt")
    store = MagicMock()
    with patch(LOADER_PATH) as mock_factory_cls:
        mock_factory_cls.return_value.load.return_value = [doc]
        engine = HierarchicalRAGEngine(
            vector_store=store,
            parent_chunk_size=200,
            child_chunk_size=50,
            child_overlap=0,
            settings=MagicMock(),
        )
        n_parents, n_children = engine.index_document(Path("file.txt"))

    # With 500 chars and parent_size=200, we expect ~3 parent chunks (200, 200, 100)
    assert n_parents >= 2
    # Each parent is split into child_size=50 pieces; totals > n_parents
    assert n_children > n_parents
    store.add_documents.assert_called_once()


def test_index_document_writes_parent_id_and_content_in_child_metadata() -> None:
    content = "X" * 100
    doc = _make_document(content, "file.txt")
    store = MagicMock()
    with patch(LOADER_PATH) as mock_factory_cls:
        mock_factory_cls.return_value.load.return_value = [doc]
        engine = HierarchicalRAGEngine(
            vector_store=store,
            parent_chunk_size=100,
            child_chunk_size=25,
            child_overlap=0,
            settings=MagicMock(),
        )
        engine.index_document(Path("file.txt"))

    # Inspect what was passed to add_documents
    called_docs = store.add_documents.call_args[0][0]
    assert len(called_docs) > 0
    for d in called_docs:
        assert "parent_id" in d.metadata
        assert "parent_content" in d.metadata
        assert "chunk_id" in d.metadata
        assert d.metadata["source"] == "file.txt"


# ── search() ─────────────────────────────────────────────────────────────────


def test_search_groups_children_by_parent_id() -> None:
    """Two child hits from the same parent → one ParentChunk, two matched_child_ids."""
    doc_c0 = _child_result("content 0", "file.txt", "c0", "p0", "PARENT-P0-BIG")
    doc_c1 = _child_result("content 1", "file.txt", "c1", "p0", "PARENT-P0-BIG")
    doc_c2 = _child_result("content 2", "file.txt", "c2", "p1", "PARENT-P1-BIG")
    store = MagicMock()
    store.similarity_search.return_value = [
        (doc_c0, 0.9),
        (doc_c1, 0.85),
        (doc_c2, 0.8),
    ]
    engine = HierarchicalRAGEngine(
        vector_store=store,
        parent_chunk_size=500,
        child_chunk_size=100,
        settings=MagicMock(),
    )
    result = engine.search("query", k=3)

    assert len(result.parent_chunks) == 2  # p0 and p1
    ids = sorted(p.parent_id for p in result.parent_chunks)
    assert ids == ["p0", "p1"]
    # matched_child_ids has all three
    assert sorted(result.matched_child_ids) == ["c0", "c1", "c2"]


def test_search_parent_chunk_aggregates_its_child_ids() -> None:
    doc_c0 = _child_result("c0", "file.txt", "c0", "p0", "PARENT-P0")
    doc_c1 = _child_result("c1", "file.txt", "c1", "p0", "PARENT-P0")
    store = MagicMock()
    store.similarity_search.return_value = [(doc_c0, 0.9), (doc_c1, 0.85)]
    engine = HierarchicalRAGEngine(
        vector_store=store, parent_chunk_size=500, child_chunk_size=100, settings=MagicMock()
    )
    result = engine.search("q", k=5)

    p0 = next(p for p in result.parent_chunks if p.parent_id == "p0")
    assert sorted(p0.child_ids) == ["c0", "c1"]
    assert p0.content == "PARENT-P0"


def test_search_preserves_parent_order_by_best_child_score() -> None:
    """Parents are ordered by the best (highest) score among their children."""
    doc_p1_best = _child_result("top hit", "file.txt", "c2", "p1", "PARENT-P1")
    doc_p0_second = _child_result("second", "file.txt", "c0", "p0", "PARENT-P0")
    store = MagicMock()
    # p1 has the top-scoring child
    store.similarity_search.return_value = [
        (doc_p1_best, 0.95),
        (doc_p0_second, 0.60),
    ]
    engine = HierarchicalRAGEngine(
        vector_store=store, parent_chunk_size=500, child_chunk_size=100, settings=MagicMock()
    )
    result = engine.search("q", k=5)
    assert result.parent_chunks[0].parent_id == "p1"
    assert result.parent_chunks[1].parent_id == "p0"


def test_search_handles_children_without_parent_metadata_gracefully() -> None:
    """A child missing parent_id metadata is dropped (logged) but does not crash."""
    bad = MagicMock()
    bad.page_content = "orphan"
    bad.metadata = {"source": "file.txt", "chunk_id": "x"}  # no parent_id
    store = MagicMock()
    store.similarity_search.return_value = [(bad, 0.9)]
    engine = HierarchicalRAGEngine(
        vector_store=store, parent_chunk_size=500, child_chunk_size=100, settings=MagicMock()
    )
    result = engine.search("q", k=1)
    assert result.parent_chunks == []
    assert result.matched_child_ids == []


def test_search_returns_at_most_k_parents() -> None:
    """k limits number of parent groups, not child hits."""
    docs = [_child_result(f"c{i}", "file.txt", f"c{i}", f"p{i}", f"PARENT-P{i}") for i in range(10)]
    store = MagicMock()
    store.similarity_search.return_value = [(d, 0.9 - i * 0.01) for i, d in enumerate(docs)]
    engine = HierarchicalRAGEngine(
        vector_store=store, parent_chunk_size=500, child_chunk_size=100, settings=MagicMock()
    )
    result = engine.search("q", k=3)
    assert len(result.parent_chunks) == 3
