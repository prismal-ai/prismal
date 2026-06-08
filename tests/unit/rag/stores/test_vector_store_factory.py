"""Unit tests for VectorStoreFactory + FakeVectorStore (SPEC-VS-008/009).

Covers backend selection, the deferred-import / extra-missing error path, and
the deterministic test double, plus structural conformance to ``VectorStorePort``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.documents import Document

from prismal.agents.extension.ports import VectorStorePort, conforms_to
from prismal.core.exceptions import VectorStoreBackendUnavailable
from prismal.rag.vector_store_factory import FakeVectorStore, VectorStoreFactory

CHROMA_CLS = "prismal.rag.stores.chroma.Chroma"
CHROMA_EMB = "prismal.rag.stores.chroma.EmbeddingsFactory"


def _settings(backend: str) -> MagicMock:
    """Return a minimal settings double exposing the vector-store fields."""
    s = MagicMock()
    s.vector_store_backend = backend
    s.chroma_path = "data/db/chroma"
    s.vector_store_path = "data/db/vectors"
    s.vector_store_url = None
    s.resolve_vector_store_path.return_value = "data/db/vectors"
    return s


# ── Backend selection ─────────────────────────────────────────────────────────


def test_create_chroma_returns_conforming_store() -> None:
    """Default backend builds a ChromaVectorStore conforming to the port."""
    with patch(CHROMA_CLS), patch(CHROMA_EMB) as emb:
        emb.create.return_value = MagicMock()
        store = VectorStoreFactory.create(_settings("chroma"), collection_name="docs")

    from prismal.rag.stores.chroma import ChromaVectorStore

    assert isinstance(store, ChromaVectorStore)
    assert store.collection_name == "docs"
    assert conforms_to(store, VectorStorePort)


def test_create_passes_collection_name() -> None:
    """The collection name flows through to the adapter."""
    with patch(CHROMA_CLS), patch(CHROMA_EMB) as emb:
        emb.create.return_value = MagicMock()
        store = VectorStoreFactory.create(_settings("chroma"), collection_name="my_col")
    assert store.collection_name == "my_col"


# ── Missing-extra error path ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("backend", "extra"),
    [("lancedb", "lancedb"), ("qdrant", "qdrant"), ("pgvector", "pgvector")],
)
def test_create_missing_extra_raises_backend_unavailable(backend: str, extra: str) -> None:
    """A backend whose extra is not installed raises VectorStoreBackendUnavailable.

    Skips when the optional package happens to be installed in the environment.
    """
    import importlib.util

    probe = {"lancedb": "lancedb", "qdrant": "qdrant_client", "pgvector": "langchain_postgres"}
    if importlib.util.find_spec(probe[backend]) is not None:
        pytest.skip(f"{probe[backend]} is installed; cannot test the unavailable path")

    with pytest.raises(VectorStoreBackendUnavailable) as excinfo:
        VectorStoreFactory.create(_settings(backend), collection_name="x")
    assert excinfo.value.extra == extra
    assert extra in str(excinfo.value)


# ── FakeVectorStore ───────────────────────────────────────────────────────────


def test_fake_conforms_to_port() -> None:
    """FakeVectorStore structurally satisfies VectorStorePort."""
    assert conforms_to(FakeVectorStore(), VectorStorePort)


def test_fake_similarity_search_returns_seeded_results() -> None:
    """similarity_search returns exactly the pre-seeded (Document, score) tuples."""
    doc = Document(page_content="hello", metadata={"source": "a"})
    fake = FakeVectorStore({"q": [(doc, 0.9)]})
    assert fake.similarity_search("q") == [(doc, 0.9)]
    assert fake.similarity_search("missing") == []


def test_fake_similarity_search_respects_k() -> None:
    """similarity_search truncates to k results."""
    docs = [(Document(page_content=str(i)), 1.0 - i / 10) for i in range(5)]
    fake = FakeVectorStore({"q": docs})
    assert len(fake.similarity_search("q", k=2)) == 2


def test_fake_add_and_delete_record_calls() -> None:
    """add_documents/delete_by_source/delete_collection record their inputs."""
    fake = FakeVectorStore()
    ids = fake.add_documents([Document(page_content="x"), Document(page_content="y")])
    assert ids == ["fake-0", "fake-1"]
    assert len(fake.added) == 2

    fake.delete_by_source("src")
    assert fake.deleted_sources == ["src"]

    assert fake.collection_deleted is False
    fake.delete_collection()
    assert fake.collection_deleted is True
