"""Vector store factory and test double (Phase Z — SPEC-VS-008/009).

:class:`VectorStoreFactory` selects and builds the vector-store adapter named by
``settings.vector_store_backend`` (default ``"chroma"``), the exact mirror of
:class:`~prismal.rag.embeddings.EmbeddingsFactory`. Consumers (RAG patterns and
the memory layer) call ``VectorStoreFactory.create(settings, collection)`` and
type the result against :class:`~prismal.agents.extension.ports.VectorStorePort`
— they never import a concrete adapter.

Adapter modules are imported lazily so the base install does not pull optional
backend SDKs; a missing extra surfaces as
:class:`~prismal.core.exceptions.VectorStoreBackendUnavailable` from the
adapter's constructor, guiding the operator to ``pip install 'prismal[<extra>]'``.

:class:`FakeVectorStore` is a deterministic, I/O-free double for tests: inject it
instead of standing up a real backend.

Example::

    from prismal.rag.vector_store_factory import VectorStoreFactory

    store = VectorStoreFactory.create(settings, collection_name="docs")
    store.add_documents(documents)
    hits = store.similarity_search("query", k=3)  # score in [0, 1]
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prismal.core.config import get_settings
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from langchain_core.documents import Document

    from prismal.agents.extension.ports import VectorStorePort
    from prismal.core.config import Settings

logger = get_logger("prismal.rag.vector_store_factory")


class VectorStoreFactory:
    """Select and build a ``VectorStorePort`` from settings (SPEC-VS-009).

    Mirror of :class:`~prismal.rag.embeddings.EmbeddingsFactory`: a single
    static :meth:`create` maps ``settings.vector_store_backend`` to the concrete
    adapter, importing it lazily.
    """

    @staticmethod
    def create(
        settings: Settings | None = None,
        collection_name: str = "default",
    ) -> VectorStorePort:
        """Instantiate the configured vector-store adapter.

        Args:
            settings: Application settings. ``None`` resolves via
                :func:`~prismal.core.config.get_settings`.
            collection_name: Collection/table the adapter operates on.

        Returns:
            A :class:`~prismal.agents.extension.ports.VectorStorePort`.

        Raises:
            VectorStoreBackendUnavailable: If the selected backend's optional
                extra is not installed (raised by the adapter's constructor).
            VectorStoreError: If a server backend is misconfigured (e.g. pgvector
                without a DSN).
        """
        resolved: Settings = settings if settings is not None else get_settings()
        backend = resolved.vector_store_backend

        if backend == "chroma":
            from prismal.rag.stores.chroma import ChromaVectorStore

            store: VectorStorePort = ChromaVectorStore(collection_name, resolved)
        elif backend == "lancedb":
            from prismal.rag.stores.lancedb import LanceDBVectorStore

            store = LanceDBVectorStore(collection_name, resolved)
        elif backend == "sqlite_vec":
            from prismal.rag.stores.sqlite_vec import SqliteVecVectorStore

            store = SqliteVecVectorStore(collection_name, resolved)
        elif backend == "qdrant":
            from prismal.rag.stores.qdrant import QdrantVectorStore

            store = QdrantVectorStore(collection_name, resolved)
        elif backend == "pgvector":
            from prismal.rag.stores.pgvector import PgVectorStore

            store = PgVectorStore(collection_name, resolved)
        else:  # pragma: no cover - Literal type makes this unreachable
            msg = (
                f"Unknown vector_store_backend: {backend!r}. Valid options are: "
                "'chroma', 'lancedb', 'sqlite_vec', 'qdrant', 'pgvector'."
            )
            raise ValueError(msg)

        logger.info("vector_store.created", backend=backend, collection=collection_name)
        return store


class FakeVectorStore:
    """Deterministic, I/O-free ``VectorStorePort`` double for tests (SPEC-VS-008).

    ``similarity_search`` returns ``docs.get(query, [])`` verbatim, so tests
    supply pre-scored ``(Document, score)`` tuples (scores already in ``[0, 1]``)
    and assert on exact retrieval without standing up a backend.

    Args:
        docs: Mapping of query string → list of ``(Document, score)`` results.
        collection_name: Reported collection name. Defaults to ``"fake"``.
    """

    def __init__(
        self,
        docs: dict[str, list[tuple[Document, float]]] | None = None,
        collection_name: str = "fake",
    ) -> None:
        """Initialise the FakeVectorStore."""
        self._docs: dict[str, list[tuple[Document, float]]] = docs or {}
        self._collection_name = collection_name
        self.added: list[Document] = []
        self.deleted_sources: list[str] = []
        self.collection_deleted = False

    @property
    def collection_name(self) -> str:
        """Return the configured fake collection name."""
        return self._collection_name

    def add_documents(self, documents: list[Document]) -> list[str]:
        """Record *documents* and return synthetic sequential IDs."""
        start = len(self.added)
        self.added.extend(documents)
        return [f"fake-{i}" for i in range(start, start + len(documents))]

    def similarity_search(self, query: str, k: int = 5) -> list[tuple[Document, float]]:
        """Return the pre-seeded results for *query* (top-*k*)."""
        return self._docs.get(query, [])[:k]

    def delete_by_source(self, source: str) -> None:
        """Record a best-effort delete-by-source call."""
        self.deleted_sources.append(source)

    def delete_collection(self) -> None:
        """Record a collection deletion."""
        self.collection_deleted = True


__all__ = ["FakeVectorStore", "VectorStoreFactory"]
