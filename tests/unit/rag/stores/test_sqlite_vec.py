"""Unit tests for SqliteVecVectorStore (SPEC-VS-005) against a real backend.

``sqlite-vec`` is in-process and ships in the test environment, so these tests
exercise the embedded adapter end-to-end (add → search → delete) with a
deterministic fake embedding injected in place of a real model. They assert the
SPEC-VS-002 score contract (scores in ``[0, 1]``, higher = more relevant).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.documents import Document
from langchain_core.embeddings import DeterministicFakeEmbedding

from prismal.agents.extension.ports import VectorStorePort, conforms_to

if TYPE_CHECKING:
    from pathlib import Path

pytest.importorskip("sqlite_vec")

_EMB = "prismal.rag.stores.sqlite_vec.EmbeddingsFactory"
_CORPUS = [
    Document(page_content="the cat sat on the mat", metadata={"source": "a.txt"}),
    Document(page_content="quantum entanglement is spooky", metadata={"source": "b.txt"}),
    Document(page_content="rust borrow checker semantics", metadata={"source": "c.txt"}),
]


def _settings(tmp_path: Path) -> MagicMock:
    s = MagicMock()
    s.resolve_vector_store_path.return_value = str(tmp_path)
    return s


def _build(tmp_path: Path, collection: str = "t_default"):
    from prismal.rag.stores.sqlite_vec import SqliteVecVectorStore

    with patch(_EMB) as emb:
        emb.create.return_value = DeterministicFakeEmbedding(size=32)
        return SqliteVecVectorStore(collection_name=collection, settings=_settings(tmp_path))


def test_conforms_to_port(tmp_path: Path) -> None:
    """The adapter structurally satisfies VectorStorePort."""
    store = _build(tmp_path)
    assert conforms_to(store, VectorStorePort)
    assert store.collection_name == "t_default"


def test_add_and_search_scores_in_unit_interval(tmp_path: Path) -> None:
    """Search returns scores in [0, 1] ordered descending (contract SPEC-VS-002)."""
    store = _build(tmp_path, "t_scores")
    store.add_documents(_CORPUS)

    results = store.similarity_search("the cat sat on the mat", k=3)
    assert results, "expected at least one hit"
    scores = [score for _, score in results]
    assert all(0.0 <= s <= 1.0 for s in scores)
    assert scores == sorted(scores, reverse=True)


def test_exact_match_ranks_first_with_top_score(tmp_path: Path) -> None:
    """A query identical to a document ranks it first at score ~1.0.

    Identical text → identical deterministic embedding → zero distance →
    ``from_distance(0) == 1.0``.
    """
    store = _build(tmp_path, "t_exact")
    store.add_documents(_CORPUS)

    top_doc, top_score = store.similarity_search("rust borrow checker semantics", k=3)[0]
    assert top_doc.page_content == "rust borrow checker semantics"
    assert top_score == pytest.approx(1.0)


def test_delete_by_source_removes_only_that_source(tmp_path: Path) -> None:
    """delete_by_source drops rows matching metadata['source'] only."""
    store = _build(tmp_path, "t_delete")
    store.add_documents(_CORPUS)

    store.delete_by_source("a.txt")
    remaining = store.similarity_search("the cat sat on the mat", k=10)
    sources = {doc.metadata.get("source") for doc, _ in remaining}
    assert "a.txt" not in sources


def test_delete_by_source_best_effort_on_missing_table(tmp_path: Path) -> None:
    """delete_by_source never raises, even before anything was added."""
    store = _build(tmp_path, "t_empty")
    store.delete_by_source("nope")  # must not raise


def test_delete_collection(tmp_path: Path) -> None:
    """delete_collection drops the data table without raising."""
    store = _build(tmp_path, "t_drop")
    store.add_documents(_CORPUS)
    store.delete_collection()  # must not raise
