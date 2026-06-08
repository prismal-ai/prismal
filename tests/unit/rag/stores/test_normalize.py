"""Unit tests for the score-normalization helpers (SPEC-VS-002).

The :class:`~prismal.agents.extension.ports.VectorStorePort` contract requires
``similarity_search`` to return ``score ∈ [0, 1]`` with higher = more relevant.
These tests pin the per-metric translations in
:mod:`prismal.rag.stores._normalize`.
"""

from __future__ import annotations

import pytest

from prismal.rag.stores import _normalize as norm


@pytest.mark.parametrize(
    ("value", "expected"),
    [(-1.0, 0.0), (0.0, 0.0), (0.5, 0.5), (1.0, 1.0), (2.5, 1.0)],
)
def test_clamp01(value: float, expected: float) -> None:
    """clamp01 keeps values inside [0, 1]."""
    assert norm.clamp01(value) == expected


@pytest.mark.parametrize("value", [-0.2, 0.0, 0.3, 1.0, 1.7])
def test_identity_stays_in_unit_interval(value: float) -> None:
    """identity is clamp01 (used by cosine-similarity backends)."""
    out = norm.identity(value)
    assert 0.0 <= out <= 1.0
    assert out == norm.clamp01(value)


def test_from_distance_zero_is_perfect() -> None:
    """A zero distance maps to the maximum score 1.0."""
    assert norm.from_distance(0.0) == 1.0


def test_from_distance_is_monotonic_decreasing() -> None:
    """Larger distances produce strictly smaller scores, all within [0, 1]."""
    scores = [norm.from_distance(d) for d in (0.0, 0.5, 1.0, 5.0, 100.0)]
    assert all(0.0 <= s <= 1.0 for s in scores)
    assert scores == sorted(scores, reverse=True)
    assert scores[0] > scores[-1]


def test_from_distance_negative_treated_as_zero() -> None:
    """A (degenerate) negative distance clamps to the best score."""
    assert norm.from_distance(-3.0) == 1.0


@pytest.mark.parametrize(
    ("distance", "expected"),
    [(0.0, 1.0), (1.0, 0.0), (2.0, 0.0), (0.25, 0.75)],
)
def test_from_cosine_distance(distance: float, expected: float) -> None:
    """Cosine distance d maps to 1 - d, clamped to [0, 1]."""
    assert norm.from_cosine_distance(distance) == pytest.approx(expected)
