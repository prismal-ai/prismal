"""Tests for token extraction (Phase C — SPEC-CST-USG-001)."""

from __future__ import annotations

from types import SimpleNamespace

from prismal.budget.types import TokenCounts
from prismal.budget.usage import extract_token_usage


def test_extracts_from_usage_metadata() -> None:
    """Precedence 1: LangChain's standard ``usage_metadata``."""
    msg = SimpleNamespace(
        usage_metadata={"input_tokens": 120, "output_tokens": 45, "total_tokens": 165}
    )
    tc = extract_token_usage(msg)
    assert tc == TokenCounts(prompt_tokens=120, completion_tokens=45)


def test_extracts_from_response_metadata_token_usage() -> None:
    """Precedence 2: ``response_metadata['token_usage']`` (OpenAI-style)."""
    msg = SimpleNamespace(
        usage_metadata=None,
        response_metadata={"token_usage": {"prompt_tokens": 30, "completion_tokens": 8}},
    )
    tc = extract_token_usage(msg)
    assert tc == TokenCounts(prompt_tokens=30, completion_tokens=8)


def test_usage_metadata_wins_over_response_metadata() -> None:
    msg = SimpleNamespace(
        usage_metadata={"input_tokens": 5, "output_tokens": 5},
        response_metadata={"token_usage": {"prompt_tokens": 999, "completion_tokens": 999}},
    )
    tc = extract_token_usage(msg)
    assert tc == TokenCounts(prompt_tokens=5, completion_tokens=5)


def test_missing_metadata_yields_zeros() -> None:
    assert extract_token_usage(SimpleNamespace()) == TokenCounts(0, 0)


def test_malformed_metadata_never_raises() -> None:
    """Unknown shapes degrade to zeros rather than raising."""
    assert extract_token_usage(SimpleNamespace(usage_metadata="garbage")) == TokenCounts(0, 0)
    assert extract_token_usage(SimpleNamespace(response_metadata=42)) == TokenCounts(0, 0)
    assert extract_token_usage(None) == TokenCounts(0, 0)
