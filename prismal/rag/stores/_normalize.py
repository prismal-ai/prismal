"""Score normalization helpers for vector-store adapters (SPEC-VS-002).

The :class:`~prismal.agents.extension.ports.VectorStorePort` contract requires
``similarity_search`` to return ``score ∈ [0, 1]`` with **higher = more
relevant**. Backends disagree on their native metric: Chroma already returns a
cosine similarity in ``[0, 1]`` (the reference), while LanceDB, sqlite-vec and
pgvector return a *distance* (lower = better). These pure functions translate a
native metric into the contract so the RAG patterns (``hybrid``, ``hierarchical``,
…) can fuse scores without knowing which backend is underneath.

All helpers are total: they clamp into ``[0, 1]`` so a noisy backend value can
never leak an out-of-range score into ranking.
"""

from __future__ import annotations


def clamp01(score: float) -> float:
    """Clamp *score* into the closed interval ``[0, 1]``."""
    if score < 0.0:
        return 0.0
    if score > 1.0:
        return 1.0
    return score


def identity(score: float) -> float:
    """Pass a similarity already in ``[0, 1]`` through (clamped).

    Used by backends whose native metric is a cosine similarity where higher is
    already better (Chroma reference, Qdrant cosine collections).
    """
    return clamp01(score)


def from_distance(distance: float) -> float:
    """Map a non-negative distance (lower = better) to ``[0, 1]`` (higher = better).

    Uses ``1 / (1 + d)``: ``d = 0`` → ``1.0`` (identical), and the score decays
    monotonically toward ``0`` as the distance grows. Suitable for L2 distances
    (LanceDB, sqlite-vec, pgvector ``<->``) which have no fixed upper bound.

    Negative inputs (which a well-behaved distance never produces) are treated as
    ``0`` distance and clamped to ``1.0``.
    """
    if distance <= 0.0:
        return 1.0
    return clamp01(1.0 / (1.0 + distance))


def from_cosine_distance(distance: float) -> float:
    """Map a cosine distance ``d ∈ [0, 2]`` (lower = better) to a similarity.

    Cosine distance is ``1 - cosine_similarity``; inverting it (``1 - d``)
    recovers the similarity in ``[-1, 1]``, which is then clamped to ``[0, 1]``
    (negative similarities are not relevant for ranking). Used by pgvector
    ``<=>`` and any backend reporting a cosine *distance*.
    """
    return clamp01(1.0 - distance)


__all__ = ["clamp01", "from_cosine_distance", "from_distance", "identity"]
