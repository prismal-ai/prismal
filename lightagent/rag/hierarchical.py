"""Hierarchical (parent-child) RAG.

Implements SPEC-RAG-005. Documents are split into *parent* chunks (~500 chars)
for broad context. Each parent is then split into smaller *child* chunks
(~100 chars) for high-precision similarity search. Only child chunks are
embedded; their metadata carries ``parent_id`` + ``parent_content`` so that a
search hit on a child is expanded back up to its enclosing parent block.

Result: tight retrieval precision (child granularity) with rich generation
context (parent granularity) — no need for a second vector store.

Example::

    from lightagent.rag.hierarchical import HierarchicalRAGEngine
    from lightagent.rag.vector_store import ChromaVectorStore

    store = ChromaVectorStore(collection_name="hierarchical")
    engine = HierarchicalRAGEngine(vector_store=store)
    engine.index_document(Path("notes.md"))
    result = engine.search("specific phrase", k=3)
    for parent in result.parent_chunks:
        print(parent.source, parent.content[:80])
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from langchain_core.documents import Document

from lightagent.core.config import get_settings
from lightagent.core.logging import get_logger
from lightagent.monitoring.otel import OTelManager
from lightagent.rag.loaders import DocumentProcessorFactory

if TYPE_CHECKING:
    from pathlib import Path

    from lightagent.core.config import Settings
    from lightagent.rag.vector_store import ChromaVectorStore

logger = get_logger("lightagent.rag.hierarchical")


@dataclass
class ParentChunk:
    """Broad-context parent block surfaced by a hierarchical search.

    Attributes:
        parent_id: Stable identifier for this parent block within the source.
        source: File path or URL of origin.
        content: Full parent text (typically ~500 chars).
        child_ids: IDs of the child chunks that matched the query.
    """

    parent_id: str
    source: str
    content: str
    child_ids: list[str]


@dataclass
class HierarchicalSearchResult:
    """Result of :meth:`HierarchicalRAGEngine.search`.

    Attributes:
        parent_chunks: Parent blocks to pass as context to the LLM.
        matched_child_ids: Child chunk IDs that actually matched the query
            (flat list across all parents, for debugging / audit).
    """

    parent_chunks: list[ParentChunk]
    matched_child_ids: list[str]


def _chunk_text(text: str, size: int, overlap: int) -> list[str]:
    """Simple character-based chunker with overlap.

    Avoids a langchain dependency for a deterministic, easy-to-test splitter.
    Empty strings are never returned; the final short chunk (<size) is kept.
    """
    if size <= 0:
        raise ValueError(f"size must be positive; got {size}")
    if overlap < 0 or overlap >= size:
        raise ValueError(f"overlap must be in [0, size); got {overlap}")
    if not text:
        return []
    stride = size - overlap
    chunks: list[str] = []
    i = 0
    while i < len(text):
        chunk = text[i : i + size]
        if chunk:
            chunks.append(chunk)
        if i + size >= len(text):
            break
        i += stride
    return chunks


class HierarchicalRAGEngine:
    """RAG engine with parent/child hierarchical indexing.

    Args:
        vector_store: Initialised :class:`ChromaVectorStore` for child-chunk
            similarity search.
        parent_chunk_size: Parent block size in characters (default 500).
        child_chunk_size: Child block size in characters (default 100).
        child_overlap: Overlap between consecutive child chunks (default 20).
        settings: LightAgent settings. ``None`` resolves via
            :func:`~lightagent.core.config.get_settings`.

    Raises:
        ValueError: If ``child_chunk_size >= parent_chunk_size`` or
            ``child_overlap >= child_chunk_size``.
    """

    def __init__(
        self,
        vector_store: ChromaVectorStore,
        parent_chunk_size: int = 500,
        child_chunk_size: int = 100,
        child_overlap: int = 20,
        settings: Settings | None = None,
    ) -> None:
        if child_chunk_size >= parent_chunk_size:
            raise ValueError(
                f"child_chunk_size ({child_chunk_size}) must be smaller than "
                f"parent_chunk_size ({parent_chunk_size})"
            )
        if child_overlap >= child_chunk_size:
            raise ValueError(
                f"child_overlap ({child_overlap}) must be less than "
                f"child_chunk_size ({child_chunk_size})"
            )
        self._store = vector_store
        self._parent_size = parent_chunk_size
        self._child_size = child_chunk_size
        self._child_overlap = child_overlap
        self._settings = settings if settings is not None else get_settings()

    # ── Indexing ──────────────────────────────────────────────────────────────

    def index_document(self, path: Path) -> tuple[int, int]:
        """Index *path* by splitting into parent and child chunks.

        Parent content is stored in each child's metadata under
        ``parent_content`` so that :meth:`search` can rebuild parents without
        an auxiliary store. ``delete_by_source`` is called first for
        AC-005-7 deduplication.

        Args:
            path: Path to a supported document.

        Returns:
            ``(n_parents, n_children)`` — counts of chunks created.
        """
        factory = DocumentProcessorFactory()
        docs = factory.load(path)
        source = str(path)
        logger.info("hierarchical_index_start", source=source, loaded_docs=len(docs))

        # AC-005-7 dedup
        self._store.delete_by_source(source)

        child_docs: list[Document] = []
        total_parents = 0
        for doc in docs:
            parent_texts = _chunk_text(doc.page_content, self._parent_size, overlap=0)
            for parent_text in parent_texts:
                total_parents += 1
                parent_id = uuid.uuid4().hex
                child_texts = _chunk_text(parent_text, self._child_size, self._child_overlap)
                for child_text in child_texts:
                    child_id = uuid.uuid4().hex
                    child_docs.append(
                        Document(
                            page_content=child_text,
                            metadata={
                                "source": source,
                                "chunk_id": child_id,
                                "parent_id": parent_id,
                                "parent_content": parent_text,
                            },
                        )
                    )

        if child_docs:
            self._store.add_documents(child_docs)

        logger.info(
            "hierarchical_index_done",
            source=source,
            n_parents=total_parents,
            n_children=len(child_docs),
        )
        return total_parents, len(child_docs)

    # ── Retrieval ─────────────────────────────────────────────────────────────

    def search(self, query: str, k: int = 5) -> HierarchicalSearchResult:
        """Search child chunks and expand hits to their parent blocks.

        Args:
            query: Search query.
            k: Maximum number of parent chunks to return.

        Returns:
            :class:`HierarchicalSearchResult` with unique parents and the
            flat list of matched child IDs.
        """
        otel = OTelManager()
        with otel.start_span("hierarchical.search") as span:
            span.set_attribute("lightagent.query_len", len(query))
            span.set_attribute("lightagent.k", k)

            # Over-fetch children so we have enough to fill k distinct parents.
            raw = self._store.similarity_search(query, k=k * 4)

            parent_best_score: dict[str, float] = {}
            parent_content: dict[str, str] = {}
            parent_source: dict[str, str] = {}
            parent_children: dict[str, list[str]] = {}
            matched_child_ids: list[str] = []

            for doc, score in raw:
                metadata = doc.metadata
                parent_id = metadata.get("parent_id")
                if not parent_id:
                    logger.info(
                        "hierarchical_search_child_missing_parent",
                        chunk_id=metadata.get("chunk_id"),
                    )
                    continue
                chunk_id = metadata.get("chunk_id", "unknown")
                matched_child_ids.append(chunk_id)
                if parent_id not in parent_best_score:
                    parent_best_score[parent_id] = score
                    parent_content[parent_id] = metadata.get("parent_content", "")
                    parent_source[parent_id] = metadata.get("source", "unknown")
                    parent_children[parent_id] = [chunk_id]
                else:
                    parent_best_score[parent_id] = max(parent_best_score[parent_id], score)
                    parent_children[parent_id].append(chunk_id)

            ordered_parent_ids = sorted(
                parent_best_score, key=lambda pid: parent_best_score[pid], reverse=True
            )[:k]

            parent_chunks = [
                ParentChunk(
                    parent_id=pid,
                    source=parent_source[pid],
                    content=parent_content[pid],
                    child_ids=parent_children[pid],
                )
                for pid in ordered_parent_ids
            ]

            span.set_attribute("lightagent.num_parents", len(parent_chunks))
            span.set_attribute("lightagent.num_matched_children", len(matched_child_ids))
            otel.increment_counter("rag_queries")

            logger.info(
                "hierarchical_search_done",
                n_parents=len(parent_chunks),
                n_matched_children=len(matched_child_ids),
            )
            return HierarchicalSearchResult(
                parent_chunks=parent_chunks,
                matched_child_ids=matched_child_ids,
            )


__all__ = [
    "HierarchicalRAGEngine",
    "HierarchicalSearchResult",
    "ParentChunk",
]
