"""Hybrid search — BM25 lexical + embedding semantic fusion.

Implements SPEC-RAG-003. Combines sparse lexical scoring (BM25Okapi) with
dense semantic scoring (via :class:`ChromaVectorStore`) via linear score
fusion::

    score(d) = alpha * semantic(d) + (1 - alpha) * bm25_norm(d)

The BM25 index must be built explicitly via :meth:`build_index` with a
parallel corpus and doc-id list. If :meth:`search` is called before indexing,
it falls back to pure semantic search (alpha=1.0 effectively).

Scale note: BM25Okapi is in-memory. For corpora > 100K docs, consider an
inverted-index backend (Tantivy, OpenSearch). See the task's risk log in
``specs/advanced-architectures/TASKS.md``.

Example::

    engine = HybridSearchEngine(vector_store=store, alpha=0.5)
    engine.build_index(
        corpus=["first doc text", "second doc text"],
        doc_ids=["file1.txt:0", "file2.txt:0"],
    )
    chunks = engine.search("some query", k=5)
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from rank_bm25 import BM25Okapi

from prismal.core.config import get_settings
from prismal.core.logging import get_logger
from prismal.monitoring.otel import OTelManager
from prismal.rag.crag import RetrievedChunk

if TYPE_CHECKING:
    from prismal.agents.extension.ports import VectorStorePort
    from prismal.core.config import Settings

logger = get_logger("prismal.rag.hybrid")

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    """Lowercase word-tokenize for BM25 indexing/scoring."""
    return _TOKEN_RE.findall(text.lower())


def _validate_alpha(alpha: float) -> None:
    if not (0.0 <= alpha <= 1.0):
        raise ValueError(f"alpha must be in [0.0, 1.0]; got {alpha}")


class HybridSearchEngine:
    """Hybrid BM25 + semantic retriever.

    Args:
        vector_store: Initialised :class:`ChromaVectorStore` for semantic
            search.
        alpha: Weight of the semantic score in ``[0, 1]``. ``1.0`` = pure
            semantic; ``0.0`` = pure BM25; ``0.5`` = balanced (default).
        settings: Prismal settings. ``None`` resolves via
            :func:`~prismal.core.config.get_settings`.
    """

    def __init__(
        self,
        vector_store: VectorStorePort,
        alpha: float = 0.5,
        settings: Settings | None = None,
    ) -> None:
        _validate_alpha(alpha)
        self._store = vector_store
        self._alpha = alpha
        self._settings = settings if settings is not None else get_settings()
        self._bm25: BM25Okapi | None = None
        self._corpus: list[str] = []
        self._doc_ids: list[str] = []

    def has_index(self) -> bool:
        """Return whether :meth:`build_index` has been called successfully."""
        return self._bm25 is not None

    def build_index(self, corpus: list[str], doc_ids: list[str]) -> None:
        """Build the BM25 index over *corpus*.

        Args:
            corpus: Document texts.
            doc_ids: Parallel identifiers — typically ``"{source}:{chunk_id}"``.

        Raises:
            ValueError: If ``len(corpus) != len(doc_ids)``.
        """
        if len(corpus) != len(doc_ids):
            raise ValueError(f"corpus and doc_ids length mismatch: {len(corpus)} vs {len(doc_ids)}")
        tokenized = [_tokenize(text) for text in corpus]
        self._bm25 = BM25Okapi(tokenized) if tokenized else None
        self._corpus = list(corpus)
        self._doc_ids = list(doc_ids)
        logger.info("hybrid_index_built", n_docs=len(corpus))

    def search(
        self,
        query: str,
        k: int = 5,
        alpha: float | None = None,
    ) -> list[RetrievedChunk]:
        """Hybrid search combining BM25 and semantic scores.

        Args:
            query: Search query.
            k: Max number of results.
            alpha: Optional per-call override of the semantic weight.

        Returns:
            Up to *k* :class:`RetrievedChunk` objects ordered by fused score.

        Raises:
            ValueError: If *alpha* is out of ``[0.0, 1.0]``.
        """
        effective_alpha = self._alpha if alpha is None else alpha
        _validate_alpha(effective_alpha)

        otel = OTelManager()
        with otel.start_span("hybrid.search") as span:
            span.set_attribute("prismal.alpha", effective_alpha)
            span.set_attribute("prismal.k", k)

            semantic_chunks = self._semantic_search(query, k)
            bm25_scores = self._bm25_scores(query) if self._bm25 is not None else {}

            # When no BM25 index is built, degrade gracefully to semantic-only.
            if not bm25_scores:
                logger.info("hybrid_search_semantic_only", reason="no_bm25_index")
                otel.increment_counter("rag_queries")
                return semantic_chunks[:k]

            sem_scores_by_key = {(c.source, c.chunk_id): c.relevance_score for c in semantic_chunks}
            chunk_by_key: dict[tuple[str, str], RetrievedChunk] = {
                (c.source, c.chunk_id): c for c in semantic_chunks
            }

            # Normalise BM25 to [0, 1] per query.
            max_bm25 = max(bm25_scores.values()) if bm25_scores else 0.0
            norm_bm25 = {
                doc_id: (score / max_bm25 if max_bm25 > 0 else 0.0)
                for doc_id, score in bm25_scores.items()
            }

            # Fuse: union of both candidate sets.
            fused: dict[tuple[str, str], float] = {}

            for key, sem_score in sem_scores_by_key.items():
                doc_id = f"{key[0]}:{key[1]}"
                bm = norm_bm25.get(doc_id, 0.0)
                fused[key] = effective_alpha * sem_score + (1 - effective_alpha) * bm

            for doc_id, bm_norm in norm_bm25.items():
                key = self._doc_id_to_key(doc_id)
                if key in fused:
                    continue
                sem_score = sem_scores_by_key.get(key, 0.0)
                fused[key] = effective_alpha * sem_score + (1 - effective_alpha) * bm_norm
                # Build a chunk from the corpus if the semantic side didn't return it
                if key not in chunk_by_key:
                    chunk_by_key[key] = self._chunk_from_doc_id(doc_id, bm_norm)

            ordered_keys = sorted(fused, key=lambda kk: fused[kk], reverse=True)[:k]
            results = [
                RetrievedChunk(
                    source=chunk_by_key[key].source,
                    chunk_id=chunk_by_key[key].chunk_id,
                    relevance_score=fused[key],
                    content=chunk_by_key[key].content,
                )
                for key in ordered_keys
            ]
            span.set_attribute("prismal.num_results", len(results))
            otel.increment_counter("rag_queries")
            return results

    # ── internals ─────────────────────────────────────────────────────────────

    def _semantic_search(self, query: str, k: int) -> list[RetrievedChunk]:
        raw = self._store.similarity_search(query, k=k)
        chunks: list[RetrievedChunk] = []
        for i, (doc, score) in enumerate(raw):
            chunks.append(
                RetrievedChunk(
                    source=doc.metadata.get("source", "unknown"),
                    chunk_id=doc.metadata.get("chunk_id", str(i)),
                    relevance_score=score,
                    content=doc.page_content,
                )
            )
        return chunks

    def _bm25_scores(self, query: str) -> dict[str, float]:
        """Return ``{doc_id: raw_bm25_score}`` for the query.

        Guarded by caller: ``self._bm25`` is known non-None here.
        """
        assert self._bm25 is not None
        tokens = _tokenize(query)
        if not tokens:
            return {}
        raw_scores = self._bm25.get_scores(tokens)
        return {
            doc_id: float(score) for doc_id, score in zip(self._doc_ids, raw_scores, strict=True)
        }

    def _doc_id_to_key(self, doc_id: str) -> tuple[str, str]:
        """Parse a ``"source:chunk_id"`` doc_id back into its key."""
        if ":" in doc_id:
            source, chunk_id = doc_id.rsplit(":", 1)
            return source, chunk_id
        return doc_id, "0"

    def _chunk_from_doc_id(self, doc_id: str, score: float) -> RetrievedChunk:
        """Build a RetrievedChunk from the BM25 corpus for a BM25-only hit."""
        source, chunk_id = self._doc_id_to_key(doc_id)
        try:
            idx = self._doc_ids.index(doc_id)
            content = self._corpus[idx]
        except ValueError:
            content = ""
        return RetrievedChunk(
            source=source,
            chunk_id=chunk_id,
            relevance_score=score,
            content=content,
        )


__all__ = ["HybridSearchEngine"]
