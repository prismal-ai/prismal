"""Cross-encoder reranker adapter for :class:`RerankerPort`.

(Phase TKE — SPEC-TKE-RAG-001)

``sentence_transformers`` is an optional dependency (``[rerank]`` extra) and is
imported lazily on first use. When the extra is missing the reranker logs once
(``rerank_extra_missing``) and falls back to :class:`ScoreReranker` behaviour;
it never raises.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from prismal.core.logging import get_logger
from prismal.rag.context_budget import RerankerPort, ScoreReranker

if TYPE_CHECKING:
    from collections.abc import Sequence

    from prismal.core.config import Settings
    from prismal.rag.crag import RetrievedChunk

logger = get_logger(__name__)

DEFAULT_CROSS_ENCODER = "cross-encoder/ms-marco-MiniLM-L-6-v2"

# Module-level "log once" guard for the missing extra (reset in tests).
_EXTRA_WARNED = False
_WARN_COUNT = 0


def _warn_missing() -> None:
    global _EXTRA_WARNED, _WARN_COUNT
    if _EXTRA_WARNED:
        return
    _EXTRA_WARNED = True
    _WARN_COUNT += 1
    logger.warning("rerank_extra_missing", hint="pip install 'prismal[rerank]'")


class CrossEncoderReranker:
    """Rerank chunks with a sentence-transformers cross-encoder.

    (Phase TKE — SPEC-TKE-RAG-001) Returns new :class:`RetrievedChunk`
    instances whose ``relevance_score`` is the cross-encoder score.
    """

    def __init__(self, model_name: str = DEFAULT_CROSS_ENCODER) -> None:
        self.model_name = model_name
        self._model: Any = None
        self._unavailable = False
        self._fallback = ScoreReranker()

    def _load(self) -> Any:
        if self._model is None and not self._unavailable:
            try:
                from sentence_transformers import CrossEncoder
            except ImportError:
                self._unavailable = True
                _warn_missing()
                return None
            self._model = CrossEncoder(self.model_name)
        return self._model

    def rerank(
        self, query: str, chunks: Sequence[RetrievedChunk], top_k: int
    ) -> list[RetrievedChunk]:
        """Score ``(query, content)`` pairs and return the *top_k* best."""
        if not chunks:
            return []
        try:
            model = self._load()
            if model is None:
                return self._fallback.rerank(query, chunks, top_k)
            scores = model.predict([(query, c.content) for c in chunks])
            rescored = [
                replace(c, relevance_score=float(s)) for c, s in zip(chunks, scores, strict=True)
            ]
        except Exception as exc:
            logger.warning("rerank_failed_fallback", error=type(exc).__name__)
            return self._fallback.rerank(query, chunks, top_k)
        rescored.sort(key=lambda c: c.relevance_score, reverse=True)
        return rescored[: max(top_k, 0)]


def build_reranker(settings: Settings) -> RerankerPort:
    """Select the reranker from *settings* (Phase TKE — SPEC-TKE-RAG-001).

    :class:`CrossEncoderReranker` when the master flag and
    ``rag_rerank_enabled`` are both on, else :class:`ScoreReranker`.
    """
    if settings.token_efficiency_enabled and settings.rag_rerank_enabled:
        return CrossEncoderReranker()
    return ScoreReranker()


__all__ = ["DEFAULT_CROSS_ENCODER", "CrossEncoderReranker", "build_reranker"]
