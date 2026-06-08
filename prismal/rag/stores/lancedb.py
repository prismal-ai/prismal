"""LanceDB vector store adapter (Phase Z — embedded backend, ``[lancedb]``).

LanceDB is an embedded, serverless vector database: it persists to a local
directory and opens **no network port**, structurally removing the server-CVE
risk family. This adapter wraps ``langchain_community.vectorstores.LanceDB`` and
conforms to :class:`~prismal.agents.extension.ports.VectorStorePort`.

Score contract (SPEC-VS-002): LanceDB's ``similarity_search_with_score`` returns
a *distance* (lower = more relevant). It is normalized to ``[0, 1]``
(higher = better) via :func:`prismal.rag.stores._normalize.from_distance`.

The ``lancedb`` import is **deferred** to construction time; if the extra is not
installed a :class:`~prismal.core.exceptions.VectorStoreBackendUnavailable` is
raised pointing at ``pip install 'prismal[lancedb]'``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prismal.core.config import get_settings
from prismal.core.exceptions import VectorStoreBackendUnavailable, VectorStoreError
from prismal.core.logging import get_logger
from prismal.rag.embeddings import EmbeddingsFactory
from prismal.rag.stores._normalize import from_distance

if TYPE_CHECKING:
    from langchain_core.documents import Document

    from prismal.core.config import Settings

logger = get_logger("prismal.rag.stores.lancedb")

_EXTRA = "lancedb"


class LanceDBVectorStore:
    """Embedded LanceDB adapter conforming to ``VectorStorePort``.

    Args:
        collection_name: LanceDB table name. Defaults to ``"default"``.
        settings: Application settings. ``None`` resolves via ``get_settings()``.
            Persistence directory is ``settings.resolve_vector_store_path()``.
    """

    def __init__(
        self,
        collection_name: str = "default",
        settings: Settings | None = None,
    ) -> None:
        """Initialise the LanceDB store (deferred import of ``lancedb``)."""
        try:
            import lancedb
            from langchain_community.vectorstores import LanceDB
        except ImportError as exc:  # pragma: no cover - exercised via factory test
            raise VectorStoreBackendUnavailable(backend="lancedb", extra=_EXTRA) from exc

        resolved: Settings = settings if settings is not None else get_settings()
        self._collection_name = collection_name
        embeddings = EmbeddingsFactory.create(settings=resolved)

        connection = lancedb.connect(resolved.resolve_vector_store_path())
        self._lancedb: LanceDB = LanceDB(
            connection=connection,
            embedding=embeddings,
            table_name=collection_name,
        )

    @property
    def collection_name(self) -> str:
        """Return the active LanceDB table name."""
        return self._collection_name

    def add_documents(self, documents: list[Document]) -> list[str]:
        """Add documents to the LanceDB table and return their IDs."""
        logger.info(
            "vector_store_add_documents",
            collection=self._collection_name,
            document_count=len(documents),
        )
        try:
            return self._lancedb.add_documents(documents)
        except Exception as exc:
            raise VectorStoreError(str(exc)) from exc

    def similarity_search(self, query: str, k: int = 5) -> list[tuple[Document, float]]:
        """Return the top-*k* ``(Document, score)`` with ``score ∈ [0, 1]``.

        The native LanceDB distance (lower = better) is normalized via
        :func:`from_distance`.
        """
        logger.info(
            "vector_store_similarity_search",
            collection=self._collection_name,
            query=query,
            k=k,
        )
        try:
            raw = self._lancedb.similarity_search_with_score(query, k=k)
        except Exception as exc:
            raise VectorStoreError(str(exc)) from exc
        return [(doc, from_distance(float(distance))) for doc, distance in raw]

    def delete_by_source(self, source: str) -> None:
        """Best-effort delete of documents where ``metadata["source"] == source``."""
        logger.info(
            "vector_store_delete_by_source",
            collection=self._collection_name,
            source=source,
        )
        escaped = source.replace("'", "''")
        try:
            self._lancedb.delete(filter=f"source = '{escaped}'")
        except Exception as exc:
            logger.debug(
                "vector_store_delete_by_source_noop",
                collection=self._collection_name,
                source=source,
                reason=str(exc),
            )

    def delete_collection(self) -> None:
        """Drop the entire LanceDB table."""
        try:
            self._lancedb.delete(delete_all=True)
        except Exception as exc:
            raise VectorStoreError(str(exc)) from exc


__all__ = ["LanceDBVectorStore"]
