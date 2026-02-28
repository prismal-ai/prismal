"""ChromaDB vector store wrapper for the LightAgent RAG system.

Provides ``ChromaVectorStore``, a thin wrapper around
:class:`~langchain_community.vectorstores.Chroma` that integrates with the
LightAgent settings and embeddings systems.

The wrapper handles:
- Embedding initialisation via :class:`~lightagent.rag.embeddings.EmbeddingsFactory`
- Persistence directory from :class:`~lightagent.core.config.Settings`
- Document addition, similarity search, and collection deletion
- Structured logging via structlog

Note: Document deduplication by source metadata is handled at the caller
(engine.py) layer, not here.  ``add_documents`` always adds new vectors.

Example::

    from lightagent.rag.vector_store import ChromaVectorStore

    store = ChromaVectorStore(collection_name="docs")
    ids = store.add_documents(documents)
    results = store.similarity_search("What is LightAgent?", k=3)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_community.vectorstores import Chroma

from lightagent.core.config import get_settings
from lightagent.core.exceptions import LightAgentError
from lightagent.core.logging import get_logger
from lightagent.rag.embeddings import EmbeddingsFactory

if TYPE_CHECKING:
    from langchain_core.documents import Document

    from lightagent.core.config import Settings

logger = get_logger("lightagent.rag.vector_store")


# ── Exception ─────────────────────────────────────────────────────────────────


class ChromaStoreError(LightAgentError):
    """Raised when a ChromaDB vector store operation fails."""


# ── Vector store ──────────────────────────────────────────────────────────────


class ChromaVectorStore:
    """Wrapper around ``langchain_community.vectorstores.Chroma``.

    Integrates ChromaDB with the LightAgent settings and embeddings systems.
    All ChromaDB interactions are delegated to the underlying ``Chroma``
    instance obtained at construction time.

    Args:
        collection_name: Name of the ChromaDB collection to use.  Defaults to
            ``"default"``.
        settings: Application settings.  When ``None``, resolved automatically
            via :func:`~lightagent.core.config.get_settings`.
    """

    def __init__(
        self,
        collection_name: str = "default",
        settings: Settings | None = None,
    ) -> None:
        """Initialise the ChromaVectorStore.

        Calls :meth:`~lightagent.rag.embeddings.EmbeddingsFactory.create` to
        obtain the configured embeddings implementation, then constructs a
        ``Chroma`` instance pointing at ``settings.chroma_path``.

        Args:
            collection_name: ChromaDB collection name.  Defaults to
                ``"default"``.
            settings: LightAgent settings.  ``None`` uses ``get_settings()``.
        """
        resolved: Settings = settings if settings is not None else get_settings()
        self._collection_name = collection_name

        embeddings = EmbeddingsFactory.create(settings=resolved)
        self._chroma: Chroma = Chroma(
            collection_name=collection_name,
            embedding_function=embeddings,
            persist_directory=resolved.chroma_path,
        )

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def collection_name(self) -> str:
        """Return the name of the active ChromaDB collection.

        Returns:
            The collection name string passed at construction time.
        """
        return self._collection_name

    # ── Public API ────────────────────────────────────────────────────────────

    def add_documents(self, documents: list[Document]) -> list[str]:
        """Add documents to the ChromaDB collection.

        Delegates directly to
        :meth:`~langchain_community.vectorstores.Chroma.add_documents`.
        Deduplication by source metadata is the responsibility of the caller.

        Args:
            documents: List of :class:`~langchain_core.documents.Document`
                objects to index.

        Returns:
            List of string IDs assigned to the added documents by ChromaDB.

        Raises:
            ChromaStoreError: If the underlying Chroma call raises an exception.
        """
        count = len(documents)
        logger.info(
            "vector_store_add_documents",
            collection=self._collection_name,
            document_count=count,
        )
        return self._chroma.add_documents(documents)

    def similarity_search(
        self,
        query: str,
        k: int = 5,
    ) -> list[tuple[Document, float]]:
        """Search the collection for the most similar documents.

        Uses
        :meth:`~langchain_community.vectorstores.Chroma.similarity_search_with_score`
        so that relevance scores are included in the returned results.

        Args:
            query: The search query string.
            k: Number of top results to return.  Defaults to ``5``.

        Returns:
            List of ``(Document, score)`` tuples, ordered by descending
            similarity.  Scores are cosine-similarity values in ``[0, 1]``.

        Raises:
            ChromaStoreError: If the underlying Chroma call raises an exception.
        """
        logger.info(
            "vector_store_similarity_search",
            collection=self._collection_name,
            query=query,
            k=k,
        )
        return self._chroma.similarity_search_with_score(query, k=k)

    def delete_by_source(self, source: str) -> None:
        """Delete all documents whose ``metadata["source"]`` matches *source*.

        Used by the engine layer to implement duplicate-prevention (AC-005-7):
        before re-indexing a file, its existing vectors are deleted so that
        re-indexing does not accumulate duplicate chunks.

        The operation is best-effort: if the underlying Chroma call raises
        (e.g. the collection is empty), the error is silently swallowed because
        there is nothing to delete.

        Args:
            source: The source metadata value to match against — typically the
                absolute path string of the file being re-indexed.
        """
        logger.info(
            "vector_store_delete_by_source",
            collection=self._collection_name,
            source=source,
        )
        try:
            self._chroma.delete(where={"source": source})
        except Exception as exc:
            logger.debug(
                "vector_store_delete_by_source_noop",
                collection=self._collection_name,
                source=source,
                reason=str(exc),
            )

    def delete_collection(self) -> None:
        """Delete the entire ChromaDB collection.

        All vectors and metadata stored in the collection are permanently
        removed.  This action cannot be undone.

        Raises:
            ChromaStoreError: If the underlying Chroma call raises an exception.
        """
        self._chroma.delete_collection()


__all__ = ["ChromaStoreError", "ChromaVectorStore"]
