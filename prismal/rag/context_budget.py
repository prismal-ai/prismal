"""RAG context budgeting: rerank, compress prose, fit to a token budget.

(Phase TKE — SPEC-TKE-RAG-001)

Retrieval fetches ``rag_retrieve_k`` candidates; this module narrows them to
what fits ``rag_context_budget_tokens`` (estimated tokens). It depends only on
two ports — :class:`RerankerPort` and :class:`PassageCompressorPort` — whose
model-backed adapters live in ``prismal/providers/`` (``rerank.py`` and
``compression.py``). This module never imports a provider library.

Gated by ``token_efficiency_enabled`` (master) **and**
``rag_context_budget_enabled``; with either off :func:`budget_context` returns
the chunks unchanged (pre-TKE behaviour).
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import replace
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from prismal.core.logging import get_logger
from prismal.monitoring.otel import OTelManager
from prismal.utils.token_estimate import estimate_tokens

if TYPE_CHECKING:
    from collections.abc import Sequence

    from prismal.core.config import Settings
    from prismal.rag.crag import RetrievedChunk

logger = get_logger(__name__)

_MIN_COMPRESSIBLE_CHARS = 200
_CODE_LINE_RATIO = 0.30
_CODE_PREFIXES: tuple[str, ...] = (
    "def ",
    "class ",
    "import ",
    "from ",
    "function ",
    "const ",
    "return ",
    "}",
)
_CODE_SUFFIXES: tuple[str, ...] = (";", "{")


@runtime_checkable
class RerankerPort(Protocol):
    """Reorders chunks by relevance to *query* (Phase TKE — SPEC-TKE-RAG-001)."""

    def rerank(
        self, query: str, chunks: Sequence[RetrievedChunk], top_k: int
    ) -> list[RetrievedChunk]:
        """Return at most *top_k* chunks, most relevant first."""
        ...


@runtime_checkable
class PassageCompressorPort(Protocol):
    """Compresses a prose passage toward *target_tokens* (Phase TKE — SPEC-TKE-RAG-001)."""

    def compress(self, text: str, *, target_tokens: int) -> str:
        """Return a compressed version of *text* (or *text* itself)."""
        ...


class ScoreReranker:
    """Default dependency-free reranker (Phase TKE — SPEC-TKE-RAG-001).

    Dedupes by ``chunk_id`` keeping the highest score, sorts by
    ``relevance_score`` descending, then truncates to ``top_k``.
    """

    def rerank(
        self,
        query: str,  # noqa: ARG002 — port signature; score-based ranking ignores it
        chunks: Sequence[RetrievedChunk],
        top_k: int,
    ) -> list[RetrievedChunk]:
        """Rerank by existing relevance score (*query* is unused)."""
        from prismal.rag.federated import _merge_and_rerank

        return _merge_and_rerank(list(chunks))[: max(top_k, 0)]


def _record_metrics(dropped: int, kept_tokens: int) -> None:
    with contextlib.suppress(Exception):
        otel = OTelManager()
        otel.increment_counter("rag_passages_dropped", dropped)
        otel.record_histogram("rag_context_tokens", kept_tokens)


def fit_to_budget(chunks: Sequence[RetrievedChunk], budget_tokens: int) -> list[RetrievedChunk]:
    """Greedily keep chunks, in rank order, that fit *budget_tokens*.

    (Phase TKE — SPEC-TKE-RAG-001) A chunk that would overflow the remaining
    budget is skipped and iteration continues, so a later smaller chunk may
    still fit. ``budget_tokens <= 0`` keeps nothing.
    """
    kept: list[RetrievedChunk] = []
    used = 0
    if budget_tokens > 0:
        for chunk in chunks:
            cost = estimate_tokens(chunk.content)
            if used + cost <= budget_tokens:
                kept.append(chunk)
                used += cost
    _record_metrics(len(chunks) - len(kept), used)
    return kept


def _looks_like_json(stripped: str) -> bool:
    if not stripped.startswith(("{", "[")):
        return False
    try:
        json.loads(stripped)
    except ValueError:
        return False
    return True


def _is_code_line(line: str) -> bool:
    s = line.strip()
    return s.startswith(_CODE_PREFIXES) or s.endswith(_CODE_SUFFIXES)


def is_compressible_prose(text: str) -> bool:
    """Return True when *text* is prose worth compressing.

    (Phase TKE — SPEC-TKE-RAG-001) False for short text (< 200 chars), JSON,
    text containing a fenced code block, or text where ≥ 30 % of non-empty
    lines look like code.
    """
    if len(text) < _MIN_COMPRESSIBLE_CHARS:
        return False
    stripped = text.strip()
    if _looks_like_json(stripped) or "```" in text:
        return False
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return False
    code_lines = sum(1 for ln in lines if _is_code_line(ln))
    return code_lines / len(lines) < _CODE_LINE_RATIO


def _compress_chunk(chunk: RetrievedChunk, compressor: PassageCompressorPort) -> RetrievedChunk:
    if not is_compressible_prose(chunk.content):
        return chunk
    target = estimate_tokens(chunk.content) // 2
    try:
        compressed = compressor.compress(chunk.content, target_tokens=target)
    except Exception as exc:  # compressor failure keeps the original
        logger.warning("rag_passage_compression_failed", error=type(exc).__name__)
        return chunk
    if not isinstance(compressed, str) or not compressed:
        return chunk
    return replace(chunk, content=compressed)


def budget_context(
    query: str,
    chunks: Sequence[RetrievedChunk],
    *,
    settings: Settings | None = None,
    reranker: RerankerPort | None = None,
    compressor: PassageCompressorPort | None = None,
) -> list[RetrievedChunk]:
    """Rerank, optionally compress prose, and fit *chunks* to the RAG budget.

    (Phase TKE — SPEC-TKE-RAG-001) Disabled (master or sub-flag off) ⇒ returns
    ``list(chunks)`` unchanged. Never raises: a failing reranker falls back to
    :class:`ScoreReranker`; a failing compressor keeps the original passage.
    """
    if settings is None:
        from prismal.core.config import get_settings

        settings = get_settings()
    if not (settings.token_efficiency_enabled and settings.rag_context_budget_enabled):
        return list(chunks)

    top_k = len(chunks)
    active = reranker if reranker is not None else ScoreReranker()
    try:
        ranked = active.rerank(query, chunks, top_k)
    except Exception as exc:
        logger.warning("rag_rerank_failed_fallback", error=type(exc).__name__)
        ranked = ScoreReranker().rerank(query, chunks, top_k)

    if settings.rag_passage_compression_enabled and compressor is not None:
        ranked = [_compress_chunk(c, compressor) for c in ranked]

    return fit_to_budget(ranked, settings.rag_context_budget_tokens)


def context_budget_active(settings: Settings | None) -> bool:
    """Return True when the RAG context budget is in force.

    (Phase TKE — SPEC-TKE-RAG-001) Requires both ``token_efficiency_enabled``
    and ``rag_context_budget_enabled`` to be the literal ``True`` — a mock or
    otherwise non-boolean settings object never enables it, so legacy callers
    keep their exact pre-TKE behaviour.
    """
    if settings is None:
        return False
    return (
        getattr(settings, "token_efficiency_enabled", False) is True
        and getattr(settings, "rag_context_budget_enabled", False) is True
    )


def effective_k(k: int, settings: Settings | None) -> int:
    """Return how many chunks to retrieve (Phase TKE — SPEC-TKE-RAG-001).

    ``rag_retrieve_k`` when the context budget is active, else *k* unchanged.
    """
    if not context_budget_active(settings):
        return k
    assert settings is not None  # narrowed by context_budget_active
    return int(settings.rag_retrieve_k)


def apply_context_budget(
    query: str,
    chunks: Sequence[RetrievedChunk],
    settings: Settings | None,
) -> list[RetrievedChunk]:
    """Engine-facing seam: rerank, compress prose and fit *chunks* to budget.

    (Phase TKE — SPEC-TKE-RAG-001) Inactive ⇒ ``list(chunks)`` unchanged.
    Active ⇒ builds the reranker via :func:`prismal.providers.rerank.build_reranker`
    (cross-encoder when ``rag_rerank_enabled``, else score-based) and a
    :class:`~prismal.providers.compression.PassageCompressor` only when
    ``rag_passage_compression_enabled``, then delegates to
    :func:`budget_context`. Adapters are imported lazily (they defer their own
    heavy imports); any construction failure degrades to the score-based
    reranker / no compression. Never raises.
    """
    if not context_budget_active(settings):
        return list(chunks)
    assert settings is not None  # narrowed by context_budget_active

    reranker: RerankerPort | None = None
    try:
        from prismal.providers.rerank import build_reranker

        reranker = build_reranker(settings)
    except Exception as exc:
        logger.warning("rag_reranker_build_failed", error=type(exc).__name__)

    compressor: PassageCompressorPort | None = None
    if settings.rag_passage_compression_enabled:
        try:
            from prismal.providers.compression import PassageCompressor

            compressor = PassageCompressor()
        except Exception as exc:
            logger.warning("rag_compressor_build_failed", error=type(exc).__name__)

    return budget_context(
        query, chunks, settings=settings, reranker=reranker, compressor=compressor
    )


__all__ = [
    "PassageCompressorPort",
    "RerankerPort",
    "ScoreReranker",
    "apply_context_budget",
    "budget_context",
    "context_budget_active",
    "effective_k",
    "fit_to_budget",
    "is_compressible_prose",
]
