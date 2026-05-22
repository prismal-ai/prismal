"""RAG Engine — main public entry point for LightAgent's RAG system.

Provides :class:`RAGEngine`, which composes document loading
(:class:`~lightagent.rag.loaders.DocumentProcessorFactory`), the vector store
(:class:`~lightagent.rag.vector_store.ChromaVectorStore`), and the CRAG pipeline
(:class:`~lightagent.rag.crag.CRAGPipeline`) behind a single, high-level API.

SPEC-005 acceptance criteria implemented here:
- AC-005-3: ``index_file`` / ``index_directory`` — manual indexing
- AC-005-4: ``search`` — top-k results with source citations
- AC-005-5: Each :class:`~lightagent.rag.crag.RetrievedChunk` carries ``source``,
  ``chunk_id``, ``relevance_score``, and ``content``
- AC-005-7: Re-indexing a file deletes existing vectors first (no duplicates)
- AC-005-8: ``create_collection`` — per-domain collection management

Example::

    from pathlib import Path
    from prismal.rag.engine import RAGEngine

    engine = RAGEngine(collection_name="docs")
    engine.index_directory(Path("data/documents/"))
    chunks = engine.search("What is LightAgent?", k=5)
    for chunk in chunks:
        print(chunk.source, chunk.relevance_score, chunk.content[:80])
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prismal.core.config import get_settings
from prismal.core.logging import get_logger
from prismal.monitoring.otel import OTelManager
from prismal.rag.crag import CRAGPipeline, RetrievedChunk
from prismal.rag.loaders import SUPPORTED_EXTENSIONS, DocumentProcessorFactory
from prismal.rag.vector_store import ChromaVectorStore

if TYPE_CHECKING:
    from pathlib import Path

    from prismal.core.config import Settings
    from prismal.rag.crag import CRAGResult

logger = get_logger("lightagent.rag.engine")


class RAGEngine:
    """High-level entry point for the LightAgent RAG system.

    Composes document loading, ChromaDB vector storage, and the CRAG pipeline
    into a single, ergonomic interface.

    Collections allow domain-scoped separation of indexed documents; use
    :meth:`create_collection` to obtain an engine that targets a different
    ChromaDB collection.

    Args:
        collection_name: Name of the ChromaDB collection to operate on.
            Defaults to ``"default"``.
        settings: Application settings.  ``None`` resolves automatically via
            :func:`~lightagent.core.config.get_settings`.
    """

    def __init__(
        self,
        collection_name: str = "default",
        settings: Settings | None = None,
    ) -> None:
        """Initialise the RAGEngine.

        Creates a :class:`~lightagent.rag.vector_store.ChromaVectorStore` for
        the given collection and stores the resolved settings for later use by
        the CRAG pipeline and sub-components.

        Args:
            collection_name: ChromaDB collection to use.  Defaults to
                ``"default"``.
            settings: LightAgent settings.  ``None`` uses ``get_settings()``.
        """
        self._settings: Settings = settings if settings is not None else get_settings()
        self._collection_name = collection_name
        self._store: ChromaVectorStore = ChromaVectorStore(
            collection_name=collection_name,
            settings=self._settings,
        )

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def collection_name(self) -> str:
        """Return the name of the active ChromaDB collection.

        Returns:
            The collection name string passed at construction time.
        """
        return self._collection_name

    # ── Indexing ──────────────────────────────────────────────────────────────

    def index_file(self, path: Path) -> int:
        """Index a single file into the current collection.

        Implements AC-005-7 duplicate prevention: any existing vectors whose
        ``metadata["source"]`` matches ``str(path)`` are deleted before the
        new chunks are added, ensuring re-indexing does not create duplicates.

        Args:
            path: Path to the document to index.  Must have a supported
                extension; see
                :attr:`~lightagent.rag.loaders.DocumentProcessorFactory.SUPPORTED_EXTENSIONS`.

        Returns:
            The number of document chunks indexed.

        Raises:
            UnsupportedDocumentTypeError: If the file extension is not
                supported by :class:`~lightagent.rag.loaders.DocumentProcessorFactory`.
        """
        logger.info("rag_engine_index_file_start", path=str(path))

        # AC-005-7: delete any existing vectors for this source before re-adding
        self._store.delete_by_source(str(path))

        factory = DocumentProcessorFactory()
        docs = factory.load(path)
        self._store.add_documents(docs)

        chunk_count = len(docs)
        logger.info(
            "rag_engine_index_file_done",
            path=str(path),
            chunk_count=chunk_count,
        )
        return chunk_count

    def index_directory(self, path: Path) -> int:
        """Recursively index all supported files in *path*.

        Iterates over the directory tree and calls :meth:`index_file` for each
        file whose extension is in
        :attr:`~lightagent.rag.loaders.DocumentProcessorFactory.SUPPORTED_EXTENSIONS`.
        Files with unsupported extensions are silently skipped.  Per-file
        errors are logged at WARNING level but do not abort the whole run —
        indexing continues with the remaining files.

        Args:
            path: Root directory to index recursively.

        Returns:
            Total number of chunks indexed across all successfully processed
            files.
        """
        logger.info("rag_engine_index_directory_start", path=str(path))
        total_chunks = 0

        for file_path in sorted(path.rglob("*")):
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                logger.debug(
                    "rag_engine_skip_unsupported",
                    file=str(file_path),
                    suffix=file_path.suffix,
                )
                continue

            try:
                count = self.index_file(file_path)
                total_chunks += count
            except Exception as exc:
                logger.warning(
                    "rag_engine_index_file_error",
                    file=str(file_path),
                    error=str(exc),
                )

        logger.info(
            "rag_engine_index_directory_done",
            path=str(path),
            total_chunks=total_chunks,
        )
        return total_chunks

    # ── Retrieval ─────────────────────────────────────────────────────────────

    def search(self, query: str, k: int = 5) -> list[RetrievedChunk]:
        """Search the vector store for the most relevant chunks.

        Delegates to
        :meth:`~lightagent.rag.vector_store.ChromaVectorStore.similarity_search`
        and converts the raw ``(Document, float)`` tuples into
        :class:`~lightagent.rag.crag.RetrievedChunk` objects that include all
        AC-005-5 fields (``source``, ``chunk_id``, ``relevance_score``,
        ``content``).

        Args:
            query: The search query string.
            k: Maximum number of results to return.  Defaults to ``5``.

        Returns:
            List of :class:`~lightagent.rag.crag.RetrievedChunk` objects
            ordered by descending relevance.
        """
        otel = OTelManager()
        with otel.start_span("rag.search") as span:
            span.set_attribute("lightagent.query_len", len(query))
            span.set_attribute("lightagent.k", k)

            logger.info("rag_engine_search", query_len=len(query), k=k)
            raw_results = self._store.similarity_search(query, k=k)

            chunks: list[RetrievedChunk] = []
            for i, (doc, score) in enumerate(raw_results):
                chunk = RetrievedChunk(
                    source=doc.metadata.get("source", "unknown"),
                    chunk_id=doc.metadata.get("chunk_id", str(i)),
                    relevance_score=score,
                    content=doc.page_content,
                )
                chunks.append(chunk)

            span.set_attribute("lightagent.num_results", len(chunks))
            otel.increment_counter("rag_queries")

            logger.info(
                "rag_engine_search_done",
                result_count=len(chunks),
            )
            return chunks

    async def query(self, query: str) -> CRAGResult:
        """Run the full CRAG pipeline on *query*.

        Creates a fresh :class:`~lightagent.rag.crag.CRAGPipeline` using the
        current vector store and settings, then executes the five-step
        Corrective RAG flow (retrieve → grade → filter → decide → generate).

        Args:
            query: The user query string.

        Returns:
            :class:`~lightagent.rag.crag.CRAGResult` containing the generated
            answer, the list of cited source chunks, and a flag indicating
            whether the web-search fallback was activated.
        """
        otel = OTelManager()
        with otel.start_span("rag.query") as span:
            span.set_attribute("lightagent.query_len", len(query))
            logger.info("rag_engine_query", query_len=len(query))
            pipeline = CRAGPipeline(vector_store=self._store, settings=self._settings)
            result = await pipeline.run(query)
            span.set_attribute("lightagent.used_web_fallback", result.used_web_fallback)
            span.set_attribute("lightagent.num_sources", len(result.sources))
            otel.increment_counter("rag_queries")
            return result

    # ── Collection management ─────────────────────────────────────────────────

    def create_collection(self, name: str) -> RAGEngine:
        """Create or switch to a named ChromaDB collection.

        Returns a *new* :class:`RAGEngine` instance targeting the collection
        ``name``.  The current engine is not modified.

        Args:
            name: Name of the collection to create/switch to.

        Returns:
            A new :class:`RAGEngine` with ``collection_name=name`` and the
            same settings as the current engine.
        """
        logger.info(
            "rag_engine_create_collection",
            new_collection=name,
            current_collection=self._collection_name,
        )
        return RAGEngine(collection_name=name, settings=self._settings)

    def delete_collection(self) -> None:
        """Delete the current ChromaDB collection.

        Permanently removes all vectors and metadata stored in the collection.
        This action cannot be undone.
        """
        logger.info(
            "rag_engine_delete_collection",
            collection=self._collection_name,
        )
        self._store.delete_collection()


__all__ = ["RAGEngine"]
