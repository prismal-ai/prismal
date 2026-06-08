"""pgvector vector store adapter (Phase Z — server backend, ``[pgvector]``).

Stores vectors in a PostgreSQL database with the ``pgvector`` extension via
``langchain_postgres.PGVector``. This is a **server** backend: it requires
``settings.vector_store_url`` (a SQLAlchemy/psycopg DSN) and conforms to
:class:`~prismal.agents.extension.ports.VectorStorePort`.

Score contract (SPEC-VS-002): PGVector defaults to the **cosine** distance
strategy (``<=>``, lower = more relevant); the adapter normalizes it to
``[0, 1]`` (higher = better) via
:func:`prismal.rag.stores._normalize.from_cosine_distance`.

Security: a Postgres server requires auth + a private network (the operator's
responsibility — see ``docs/vector-stores.md``); the DSN is treated as a secret
and never logged. The ``langchain_postgres`` import is **deferred**; absent →
``VectorStoreBackendUnavailable`` pointing at ``pip install 'prismal[pgvector]'``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prismal.core.config import get_settings
from prismal.core.exceptions import VectorStoreBackendUnavailable, VectorStoreError
from prismal.core.logging import get_logger
from prismal.rag.embeddings import EmbeddingsFactory
from prismal.rag.stores._normalize import from_cosine_distance

if TYPE_CHECKING:
    from langchain_core.documents import Document

    from prismal.core.config import Settings

logger = get_logger("prismal.rag.stores.pgvector")

_EXTRA = "pgvector"


class PgVectorStore:
    """PostgreSQL/pgvector adapter conforming to ``VectorStorePort``.

    Args:
        collection_name: PGVector logical collection name. Defaults to ``"default"``.
        settings: Application settings. ``None`` resolves via ``get_settings()``.
            ``settings.vector_store_url`` (DSN) is mandatory.

    Raises:
        VectorStoreError: If ``vector_store_url`` is not configured.
        VectorStoreBackendUnavailable: If the ``[pgvector]`` extra is missing.
    """

    def __init__(
        self,
        collection_name: str = "default",
        settings: Settings | None = None,
    ) -> None:
        """Initialise the pgvector store (deferred import of ``langchain_postgres``)."""
        try:
            from langchain_postgres import PGVector
        except ImportError as exc:  # pragma: no cover - exercised via factory test
            raise VectorStoreBackendUnavailable(backend="pgvector", extra=_EXTRA) from exc

        resolved: Settings = settings if settings is not None else get_settings()
        if not resolved.vector_store_url:
            raise VectorStoreError(
                "pgvector backend requires settings.vector_store_url "
                "(a PostgreSQL DSN, e.g. 'postgresql+psycopg://user:pass@host:5432/db')."
            )

        self._collection_name = collection_name
        embeddings = EmbeddingsFactory.create(settings=resolved)
        self._pgvector: PGVector = PGVector(
            embeddings=embeddings,
            collection_name=collection_name,
            connection=resolved.vector_store_url,
            use_jsonb=True,
        )

    @property
    def collection_name(self) -> str:
        """Return the active PGVector collection name."""
        return self._collection_name

    def add_documents(self, documents: list[Document]) -> list[str]:
        """Add documents to the PGVector collection and return their IDs."""
        logger.info(
            "vector_store_add_documents",
            collection=self._collection_name,
            document_count=len(documents),
        )
        try:
            ids: list[str] = self._pgvector.add_documents(documents)
        except Exception as exc:
            raise VectorStoreError(str(exc)) from exc
        return ids

    def similarity_search(self, query: str, k: int = 5) -> list[tuple[Document, float]]:
        """Return the top-*k* ``(Document, score)`` with ``score ∈ [0, 1]``.

        The native cosine distance (``<=>``, lower = better) is normalized via
        :func:`from_cosine_distance`.
        """
        logger.info(
            "vector_store_similarity_search",
            collection=self._collection_name,
            query=query,
            k=k,
        )
        try:
            raw = self._pgvector.similarity_search_with_score(query, k=k)
        except Exception as exc:
            raise VectorStoreError(str(exc)) from exc
        return [(doc, from_cosine_distance(float(distance))) for doc, distance in raw]

    def delete_by_source(self, source: str) -> None:
        """Best-effort delete of documents where ``metadata["source"] == source``."""
        logger.info(
            "vector_store_delete_by_source",
            collection=self._collection_name,
            source=source,
        )
        try:
            self._pgvector.delete(filter={"source": source})
        except Exception as exc:
            logger.debug(
                "vector_store_delete_by_source_noop",
                collection=self._collection_name,
                source=source,
                reason=str(exc),
            )

    def delete_collection(self) -> None:
        """Delete the entire PGVector collection."""
        try:
            self._pgvector.delete_collection()
        except Exception as exc:
            raise VectorStoreError(str(exc)) from exc


__all__ = ["PgVectorStore"]
