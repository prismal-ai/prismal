"""sqlite-vec vector store adapter (Phase Z — embedded backend, ``[sqlite-vec]``).

``sqlite-vec`` is an in-process SQLite extension: vectors live inside a local
SQLite database file with **no network port**, the lowest-surface option
alongside LanceDB. This adapter wraps
``langchain_community.vectorstores.SQLiteVec`` and conforms to
:class:`~prismal.agents.extension.ports.VectorStorePort`.

Score contract (SPEC-VS-002): the native metric is an L2 distance
(lower = more relevant), normalized to ``[0, 1]`` (higher = better) via
:func:`prismal.rag.stores._normalize.from_distance`.

The ``sqlite_vec`` import is **deferred**; absent → ``VectorStoreBackendUnavailable``
pointing at ``pip install 'prismal[sqlite-vec]'``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from prismal.core.config import get_settings
from prismal.core.exceptions import VectorStoreBackendUnavailable, VectorStoreError
from prismal.core.logging import get_logger
from prismal.rag.embeddings import EmbeddingsFactory
from prismal.rag.stores._normalize import from_distance

if TYPE_CHECKING:
    import sqlite3

    from langchain_core.documents import Document

    from prismal.core.config import Settings

logger = get_logger("prismal.rag.stores.sqlite_vec")

_EXTRA = "sqlite-vec"
_DB_FILENAME = "sqlite_vec.db"


class SqliteVecVectorStore:
    """Embedded sqlite-vec adapter conforming to ``VectorStorePort``.

    Args:
        collection_name: SQLite table name. Defaults to ``"default"``.
        settings: Application settings. ``None`` resolves via ``get_settings()``.
            The database file lives under ``settings.resolve_vector_store_path()``.
    """

    def __init__(
        self,
        collection_name: str = "default",
        settings: Settings | None = None,
    ) -> None:
        """Initialise the sqlite-vec store (deferred import of ``sqlite_vec``)."""
        try:
            import sqlite_vec  # noqa: F401  (presence check; loaded by SQLiteVec)
            from langchain_community.vectorstores import SQLiteVec
        except ImportError as exc:  # pragma: no cover - exercised via factory test
            raise VectorStoreBackendUnavailable(backend="sqlite_vec", extra=_EXTRA) from exc

        resolved: Settings = settings if settings is not None else get_settings()
        self._collection_name = collection_name
        embeddings = EmbeddingsFactory.create(settings=resolved)

        db_dir = Path(resolved.resolve_vector_store_path())
        db_dir.mkdir(parents=True, exist_ok=True)
        db_file = str(db_dir / _DB_FILENAME)

        self._connection: sqlite3.Connection = SQLiteVec.create_connection(db_file=db_file)
        self._sqlitevec: SQLiteVec = SQLiteVec(
            table=collection_name,
            connection=self._connection,
            embedding=embeddings,
            db_file=db_file,
        )

    @property
    def collection_name(self) -> str:
        """Return the active sqlite-vec table name."""
        return self._collection_name

    def add_documents(self, documents: list[Document]) -> list[str]:
        """Add documents to the sqlite-vec table and return their IDs."""
        logger.info(
            "vector_store_add_documents",
            collection=self._collection_name,
            document_count=len(documents),
        )
        try:
            return self._sqlitevec.add_documents(documents)
        except Exception as exc:
            raise VectorStoreError(str(exc)) from exc

    def similarity_search(self, query: str, k: int = 5) -> list[tuple[Document, float]]:
        """Return the top-*k* ``(Document, score)`` with ``score ∈ [0, 1]``.

        The native L2 distance (lower = better) is normalized via
        :func:`from_distance`.
        """
        logger.info(
            "vector_store_similarity_search",
            collection=self._collection_name,
            query=query,
            k=k,
        )
        try:
            raw = self._sqlitevec.similarity_search_with_score(query, k=k)
        except Exception as exc:
            raise VectorStoreError(str(exc)) from exc
        return [(doc, from_distance(float(distance))) for doc, distance in raw]

    def delete_by_source(self, source: str) -> None:
        """Best-effort delete of rows where ``metadata["source"] == source``.

        sqlite-vec stores metadata as JSON; the deletion runs a parameterised
        SQL ``DELETE`` keyed on ``json_extract(metadata, '$.source')``. Failures
        (missing table, locked db) are swallowed — there is nothing to delete.
        """
        logger.info(
            "vector_store_delete_by_source",
            collection=self._collection_name,
            source=source,
        )
        try:
            self._connection.execute(
                f"DELETE FROM {self._collection_name} "  # noqa: S608 - table name is config, not user input
                "WHERE json_extract(metadata, '$.source') = ?",
                (source,),
            )
            self._connection.commit()
        except Exception as exc:
            logger.debug(
                "vector_store_delete_by_source_noop",
                collection=self._collection_name,
                source=source,
                reason=str(exc),
            )

    def delete_collection(self) -> None:
        """Drop the sqlite-vec data and shadow tables for this collection."""
        try:
            self._connection.execute(f"DROP TABLE IF EXISTS {self._collection_name}")
            self._connection.execute(f"DROP TABLE IF EXISTS {self._collection_name}_vec")
            self._connection.commit()
        except Exception as exc:
            raise VectorStoreError(str(exc)) from exc


__all__ = ["SqliteVecVectorStore"]
