"""Qdrant vector store adapter (Phase Z — embedded/server backend, ``[qdrant]``).

Qdrant runs either **embedded** (a local on-disk path, no server) or against a
**server** (``vector_store_url`` + optional API key). This adapter wraps
``langchain_qdrant.QdrantVectorStore`` and conforms to
:class:`~prismal.agents.extension.ports.VectorStorePort`.

Score contract (SPEC-VS-002): the adapter creates the collection with the
**cosine** distance metric, whose native ``similarity_search_with_score`` is
already a similarity in ``[0, 1]`` (higher = better) — passed through with
:func:`prismal.rag.stores._normalize.identity`.

Security: server mode requires auth + a private network (the operator's
responsibility — see ``docs/vector-stores.md``). The embedded default opens no
port. The ``qdrant_client`` / ``langchain_qdrant`` imports are **deferred**;
absent → ``VectorStoreBackendUnavailable`` pointing at
``pip install 'prismal[qdrant]'``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prismal.core.config import get_settings
from prismal.core.exceptions import VectorStoreBackendUnavailable, VectorStoreError
from prismal.core.logging import get_logger
from prismal.rag.embeddings import EmbeddingsFactory
from prismal.rag.stores._normalize import identity

if TYPE_CHECKING:
    from langchain_core.documents import Document

    from prismal.core.config import Settings

logger = get_logger("prismal.rag.stores.qdrant")

_EXTRA = "qdrant"


class QdrantVectorStore:
    """Qdrant adapter (embedded or server) conforming to ``VectorStorePort``.

    Args:
        collection_name: Qdrant collection name. Defaults to ``"default"``.
        settings: Application settings. ``None`` resolves via ``get_settings()``.
            When ``settings.vector_store_url`` is set the adapter connects to that
            server (with ``vector_store_api_key`` if present); otherwise it runs
            embedded against ``settings.resolve_vector_store_path()``.
    """

    def __init__(
        self,
        collection_name: str = "default",
        settings: Settings | None = None,
    ) -> None:
        """Initialise the Qdrant store (deferred import of ``qdrant_client``)."""
        try:
            from langchain_qdrant import QdrantVectorStore as _LCQdrant
            from qdrant_client import QdrantClient, models
        except ImportError as exc:  # pragma: no cover - exercised via factory test
            raise VectorStoreBackendUnavailable(backend="qdrant", extra=_EXTRA) from exc

        resolved: Settings = settings if settings is not None else get_settings()
        self._collection_name = collection_name
        embeddings = EmbeddingsFactory.create(settings=resolved)

        if resolved.vector_store_url:
            api_key = (
                resolved.vector_store_api_key.get_secret_value()
                if resolved.vector_store_api_key is not None
                else None
            )
            client = QdrantClient(url=resolved.vector_store_url, api_key=api_key)
        else:
            client = QdrantClient(path=resolved.resolve_vector_store_path())

        if not client.collection_exists(collection_name):
            dimension = len(embeddings.embed_query("dimension probe"))
            client.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(
                    size=dimension,
                    distance=models.Distance.COSINE,
                ),
            )

        self._models = models
        self._qdrant: _LCQdrant = _LCQdrant(
            client=client,
            collection_name=collection_name,
            embedding=embeddings,
        )

    @property
    def collection_name(self) -> str:
        """Return the active Qdrant collection name."""
        return self._collection_name

    def add_documents(self, documents: list[Document]) -> list[str]:
        """Add documents to the Qdrant collection and return their IDs."""
        logger.info(
            "vector_store_add_documents",
            collection=self._collection_name,
            document_count=len(documents),
        )
        try:
            ids: list[str] = self._qdrant.add_documents(documents)
        except Exception as exc:
            raise VectorStoreError(str(exc)) from exc
        return ids

    def similarity_search(self, query: str, k: int = 5) -> list[tuple[Document, float]]:
        """Return the top-*k* ``(Document, score)`` with ``score ∈ [0, 1]``.

        The cosine collection's native score is already a similarity in
        ``[0, 1]`` and passed through via :func:`identity`.
        """
        logger.info(
            "vector_store_similarity_search",
            collection=self._collection_name,
            query=query,
            k=k,
        )
        try:
            raw = self._qdrant.similarity_search_with_score(query, k=k)
        except Exception as exc:
            raise VectorStoreError(str(exc)) from exc
        return [(doc, identity(float(score))) for doc, score in raw]

    def delete_by_source(self, source: str) -> None:
        """Best-effort delete of points where ``metadata["source"] == source``."""
        logger.info(
            "vector_store_delete_by_source",
            collection=self._collection_name,
            source=source,
        )
        models = self._models
        try:
            self._qdrant.client.delete(
                collection_name=self._collection_name,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="metadata.source",
                                match=models.MatchValue(value=source),
                            )
                        ]
                    )
                ),
            )
        except Exception as exc:
            logger.debug(
                "vector_store_delete_by_source_noop",
                collection=self._collection_name,
                source=source,
                reason=str(exc),
            )

    def delete_collection(self) -> None:
        """Delete the entire Qdrant collection."""
        try:
            self._qdrant.client.delete_collection(collection_name=self._collection_name)
        except Exception as exc:
            raise VectorStoreError(str(exc)) from exc


__all__ = ["QdrantVectorStore"]
