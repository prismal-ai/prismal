"""Score-parity test: each adapter ranks like the reference metric (SPEC-VS-010).

Chroma — the reference adapter (DD-VS-002) — ranks by **cosine** similarity. The
base install's ``langchain_community`` ``Chroma`` class is deprecated and cannot
be instantiated under the suite's ``filterwarnings=error`` (every other unit test
mocks it), so this parity test uses the cosine ranking computed directly over the
shared deterministic embeddings as the oracle. It then asserts that the real
``sqlite-vec`` adapter agrees with that reference on the top-ranked document for
an exact-match query and honours the SPEC-VS-002 normalized-score contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from langchain_core.documents import Document
from langchain_core.embeddings import DeterministicFakeEmbedding

if TYPE_CHECKING:
    from pathlib import Path

    from prismal.agents.extension.ports import VectorStorePort

pytest.importorskip("sqlite_vec")

# Import the adapter at collection time so the ``prismal.rag`` third-party
# deprecation warning is recorded (not raised) under ``filterwarnings=error``.
from prismal.rag.stores.sqlite_vec import SqliteVecVectorStore  # noqa: E402

_CORPUS = [
    Document(page_content="the cat sat on the mat", metadata={"source": "a.txt"}),
    Document(page_content="quantum entanglement is spooky", metadata={"source": "b.txt"}),
    Document(page_content="rust borrow checker semantics", metadata={"source": "c.txt"}),
]
_QUERY = "rust borrow checker semantics"
_EMBED_SIZE = 32


def _cosine_reference_top(embedding: DeterministicFakeEmbedding, query: str) -> str:
    """Return the page_content Chroma (cosine) would rank first for *query*."""
    q = np.asarray(embedding.embed_query(query))
    best_doc, best_sim = "", -2.0
    for doc in _CORPUS:
        v = np.asarray(embedding.embed_query(doc.page_content))
        sim = float(q @ v / (np.linalg.norm(q) * np.linalg.norm(v)))
        if sim > best_sim:
            best_doc, best_sim = doc.page_content, sim
    return best_doc


def _make_sqlite_vec(tmp_path: Path, embedding: DeterministicFakeEmbedding) -> VectorStorePort:
    settings = MagicMock()
    settings.resolve_vector_store_path.return_value = str(tmp_path / "sqlite")
    with patch("prismal.rag.stores.sqlite_vec.EmbeddingsFactory") as emb:
        emb.create.return_value = embedding
        return SqliteVecVectorStore(collection_name="parity", settings=settings)


def test_sqlite_vec_top_rank_matches_cosine_reference(tmp_path: Path) -> None:
    """sqlite-vec agrees with the cosine reference on the top-ranked document."""
    embedding = DeterministicFakeEmbedding(size=_EMBED_SIZE)
    reference_top = _cosine_reference_top(embedding, _QUERY)

    store = _make_sqlite_vec(tmp_path, embedding)
    store.add_documents(_CORPUS)
    sqlite_top = store.similarity_search(_QUERY, k=3)[0][0].page_content

    assert sqlite_top == reference_top == _QUERY


def test_sqlite_vec_scores_honour_contract(tmp_path: Path) -> None:
    """sqlite-vec normalized scores stay in [0, 1] and descend (SPEC-VS-002)."""
    store = _make_sqlite_vec(tmp_path, DeterministicFakeEmbedding(size=_EMBED_SIZE))
    store.add_documents(_CORPUS)
    scores = [s for _, s in store.similarity_search(_QUERY, k=3)]
    assert all(0.0 <= s <= 1.0 for s in scores)
    assert scores == sorted(scores, reverse=True)
