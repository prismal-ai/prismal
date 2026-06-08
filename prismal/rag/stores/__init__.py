"""Vector-store adapters (Phase Z — interchangeable backend).

Each module wraps one backend behind the
:class:`~prismal.agents.extension.ports.VectorStorePort` contract:

- :mod:`prismal.rag.stores.chroma` — ``ChromaVectorStore`` (default, base install).
- :mod:`prismal.rag.stores.lancedb` — ``LanceDBVectorStore`` (embedded, ``[lancedb]``).
- :mod:`prismal.rag.stores.sqlite_vec` — ``SqliteVecVectorStore`` (embedded, ``[sqlite-vec]``).
- :mod:`prismal.rag.stores.qdrant` — ``QdrantVectorStore`` (embedded/server, ``[qdrant]``).
- :mod:`prismal.rag.stores.pgvector` — ``PgVectorStore`` (server, ``[pgvector]``).

Backends are selected at runtime by
:class:`prismal.rag.vector_store_factory.VectorStoreFactory`; backend SDK imports
are **deferred** inside each adapter so the base install stays slim. Only Chroma
is eagerly importable (it is a base dependency).
"""

from __future__ import annotations

from prismal.rag.stores.chroma import ChromaStoreError, ChromaVectorStore

__all__ = ["ChromaStoreError", "ChromaVectorStore"]
