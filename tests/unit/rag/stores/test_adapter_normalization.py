"""Adapter body + score-normalization tests for the not-installed backends.

LanceDB, Qdrant and pgvector are optional extras absent from the test
environment, so their method bodies would otherwise never run. Here the deferred
SDK imports are replaced with lightweight fakes (``sys.modules`` injection /
attribute patching) so each adapter constructs and exercises add / search /
delete — pinning the per-backend normalization (SPEC-VS-002): LanceDB & sqlite
use ``1/(1+d)``, Qdrant passes cosine similarity through, pgvector uses ``1 - d``.
"""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.documents import Document

# Top-level import so the ``prismal.rag`` third-party deprecation warning is
# recorded (not raised) under ``filterwarnings=error``.
from prismal.rag.stores.lancedb import LanceDBVectorStore
from prismal.rag.stores.pgvector import PgVectorStore
from prismal.rag.stores.qdrant import QdrantVectorStore

_DOC = Document(page_content="hello", metadata={"source": "a.txt"})
_EMB = "prismal.rag.embeddings.EmbeddingsFactory.create"


# ── LanceDB ───────────────────────────────────────────────────────────────────


def test_lancedb_normalizes_distance(tmp_path) -> None:
    """LanceDB distance 3.0 → 1/(1+3) = 0.25; add/delete delegate."""
    settings = MagicMock()
    settings.resolve_vector_store_path.return_value = str(tmp_path)

    fake_store = MagicMock()
    fake_store.similarity_search_with_score.return_value = [(_DOC, 3.0)]
    fake_store.add_documents.return_value = ["id-0"]

    fake_lancedb = ModuleType("lancedb")
    fake_lancedb.connect = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]

    with (
        patch.dict(sys.modules, {"lancedb": fake_lancedb}),
        patch("langchain_community.vectorstores.LanceDB", return_value=fake_store),
        patch(_EMB, return_value=MagicMock()),
    ):
        store = LanceDBVectorStore("c", settings)
        assert store.add_documents([_DOC]) == ["id-0"]
        results = store.similarity_search("q", k=1)
        store.delete_by_source("a.txt")
        store.delete_collection()

    assert results == [(_DOC, pytest.approx(0.25))]
    fake_store.delete.assert_called()


# ── Qdrant ────────────────────────────────────────────────────────────────────


def _install_qdrant_fakes(fake_store: MagicMock) -> dict[str, ModuleType]:
    client = MagicMock()
    client.collection_exists.return_value = True  # skip create_collection branch

    qc = ModuleType("qdrant_client")
    qc.QdrantClient = MagicMock(return_value=client)  # type: ignore[attr-defined]
    qc.models = MagicMock()  # type: ignore[attr-defined]

    lq = ModuleType("langchain_qdrant")
    lq.QdrantVectorStore = MagicMock(return_value=fake_store)  # type: ignore[attr-defined]
    return {"qdrant_client": qc, "langchain_qdrant": lq}


def test_qdrant_server_creates_missing_collection() -> None:
    """Server mode (vector_store_url set) creates the collection when absent."""
    settings = MagicMock()
    settings.vector_store_url = "http://qdrant.internal:6333"
    settings.vector_store_api_key = None

    fake_store = MagicMock()
    fake_store.similarity_search_with_score.return_value = [(_DOC, 0.5)]

    fakes = _install_qdrant_fakes(fake_store)
    client = fakes["qdrant_client"].QdrantClient.return_value  # type: ignore[attr-defined]
    client.collection_exists.return_value = False  # force create_collection branch
    embeddings = MagicMock()
    embeddings.embed_query.return_value = [0.0, 1.0, 2.0]

    with (
        patch.dict(sys.modules, fakes),
        patch(_EMB, return_value=embeddings),
    ):
        store = QdrantVectorStore("c", settings)
        results = store.similarity_search("q", k=1)

    client.create_collection.assert_called_once()
    embeddings.embed_query.assert_called()  # dimension probe
    assert results == [(_DOC, pytest.approx(0.5))]


def test_qdrant_passes_cosine_similarity_through(tmp_path) -> None:
    """Qdrant cosine similarity 0.8 is returned as-is (identity)."""
    settings = MagicMock()
    settings.vector_store_url = None
    settings.resolve_vector_store_path.return_value = str(tmp_path)

    fake_store = MagicMock()
    fake_store.similarity_search_with_score.return_value = [(_DOC, 0.8)]
    fake_store.add_documents.return_value = ["id-0"]
    fake_store.client = MagicMock()

    with (
        patch.dict(sys.modules, _install_qdrant_fakes(fake_store)),
        patch(_EMB, return_value=MagicMock()),
    ):
        store = QdrantVectorStore("c", settings)
        assert store.add_documents([_DOC]) == ["id-0"]
        results = store.similarity_search("q", k=1)
        store.delete_by_source("a.txt")
        store.delete_collection()

    assert results == [(_DOC, pytest.approx(0.8))]
    fake_store.client.delete.assert_called()
    fake_store.client.delete_collection.assert_called()


# ── pgvector ──────────────────────────────────────────────────────────────────


def test_pgvector_normalizes_cosine_distance(tmp_path) -> None:
    """pgvector cosine distance 0.25 → 1 - 0.25 = 0.75; requires a DSN."""
    settings = MagicMock()
    settings.vector_store_url = "postgresql+psycopg://u:p@h:5432/db"

    fake_store = MagicMock()
    fake_store.similarity_search_with_score.return_value = [(_DOC, 0.25)]
    fake_store.add_documents.return_value = ["id-0"]

    lp = ModuleType("langchain_postgres")
    lp.PGVector = MagicMock(return_value=fake_store)  # type: ignore[attr-defined]

    with (
        patch.dict(sys.modules, {"langchain_postgres": lp}),
        patch(_EMB, return_value=MagicMock()),
    ):
        store = PgVectorStore("c", settings)
        assert store.add_documents([_DOC]) == ["id-0"]
        results = store.similarity_search("q", k=1)
        store.delete_by_source("a.txt")
        store.delete_collection()

    assert results == [(_DOC, pytest.approx(0.75))]
    fake_store.delete.assert_called()
    fake_store.delete_collection.assert_called()


def test_pgvector_requires_dsn() -> None:
    """pgvector without vector_store_url raises a clear VectorStoreError."""
    from prismal.core.exceptions import VectorStoreError

    settings = MagicMock()
    settings.vector_store_url = None

    lp = ModuleType("langchain_postgres")
    lp.PGVector = MagicMock()  # type: ignore[attr-defined]

    with (
        patch.dict(sys.modules, {"langchain_postgres": lp}),
        pytest.raises(VectorStoreError, match="vector_store_url"),
    ):
        PgVectorStore("c", settings)
