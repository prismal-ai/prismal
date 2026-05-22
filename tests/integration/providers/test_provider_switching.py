"""
Integration tests for live provider switching.

These tests require real API keys and are skipped in CI unless the
corresponding environment variables are set.

Run manually::

    PRISMAL_ANTHROPIC_API_KEY=sk-ant-... \\
        uv run pytest tests/integration/providers/ -v
"""

from __future__ import annotations

import os

import pytest
from langchain_core.messages import HumanMessage

from prismal.providers.registry import ProviderRegistry


def _has_env(var: str) -> bool:
    """Return True if *var* is set and non-empty in the environment."""
    return bool(os.getenv(var))


@pytest.mark.skipif(
    not _has_env("PRISMAL_ANTHROPIC_API_KEY"),
    reason="PRISMAL_ANTHROPIC_API_KEY not set",
)
def test_anthropic_live_call() -> None:
    """Live call to Anthropic Claude must return a non-empty response."""
    registry = ProviderRegistry()
    llm = registry.get_llm(model="claude-haiku-4-5-20251001", temperature=0.0)
    response = llm.invoke([HumanMessage(content="Say 'pong' and nothing else.")])
    assert isinstance(response.content, str)
    assert "pong" in response.content.lower()


@pytest.mark.skipif(
    not _has_env("PRISMAL_OPENAI_API_KEY"),
    reason="PRISMAL_OPENAI_API_KEY not set",
)
def test_openai_live_call() -> None:
    """Live call to OpenAI GPT must return a non-empty response."""
    registry = ProviderRegistry()
    llm = registry.get_llm(model="gpt-4o-mini", temperature=0.0)
    response = llm.invoke([HumanMessage(content="Say 'pong' and nothing else.")])
    assert isinstance(response.content, str)
    assert "pong" in response.content.lower()
