"""Live provider calls through ``ProviderRegistry`` → LiteLLM (opt-in: ``-m live_api``).

They live under ``tests/live`` on purpose: ``tests/integration/conftest.py`` replaces
``langchain_litellm`` with a ``MagicMock`` stub, so a "live" test there never reaches the
API. Run them after every LiteLLM bump::

    ANTHROPIC_API_KEY=... uv run pytest -m live_api tests/live/providers -v

Each test sends a few tokens to the cheapest model of the provider.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from prismal.budget.usage import extract_token_usage
from prismal.core.config import Settings
from prismal.providers.cost import compute_cost_usd
from prismal.providers.registry import ProviderRegistry

pytestmark = pytest.mark.live_api

ANTHROPIC = "anthropic/claude-haiku-4-5-20251001"
OPENAI = "gpt-4o-mini"


def _env_key(name: str) -> bool:
    """Key visible to the test body. The root conftest hides ``.env`` from tests, so a
    skip decided by ``get_settings()`` at import (which still reads ``.env``) would run
    the test without a key; only the process environment counts here."""
    return bool(os.environ.get(name) or os.environ.get(f"PRISMAL_{name}"))


_NO_ANTHROPIC = pytest.mark.skipif(
    not _env_key("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not in the environment"
)
_NO_OPENAI = pytest.mark.skipif(
    not _env_key("OPENAI_API_KEY"), reason="OPENAI_API_KEY not in the environment"
)


@tool
def get_weather(city: str) -> str:
    """Return the weather for a city."""
    return "sunny"


def _llm(model: str, **kwargs: Any) -> Any:
    llm = ProviderRegistry().get_llm(model=model, temperature=0.0, **kwargs)
    # Guard: a stubbed ChatLiteLLM would make every assertion below meaningless.
    assert "Mock" not in type(llm).__name__, f"LLM is a test stub: {type(llm)!r}"
    return llm


@_NO_ANTHROPIC
def test_anthropic_invoke_usage_and_pricing() -> None:
    response = _llm(ANTHROPIC).invoke([HumanMessage(content="Say 'pong' and nothing else.")])
    assert isinstance(response.content, str)
    assert "pong" in response.content.lower()
    usage = extract_token_usage(response)
    assert usage.prompt_tokens > 0
    assert usage.completion_tokens > 0
    estimate = compute_cost_usd(ANTHROPIC, usage.prompt_tokens, usage.completion_tokens)
    assert estimate.source == "litellm"
    assert estimate.cost_usd > 0


@_NO_ANTHROPIC
async def test_anthropic_astream() -> None:
    llm = _llm(ANTHROPIC, streaming=True)
    chunks = [c async for c in llm.astream([HumanMessage(content="Count 1 to 5, digits only.")])]
    assert len(chunks) > 1
    assert "5" in "".join(str(c.content) for c in chunks)


@_NO_ANTHROPIC
async def test_anthropic_tool_call() -> None:
    llm = _llm(ANTHROPIC).bind_tools([get_weather])
    response = await llm.ainvoke(
        [HumanMessage(content="What's the weather in Caracas? Use the tool.")]
    )
    assert response.tool_calls
    assert response.tool_calls[0]["name"] == "get_weather"
    assert "caracas" in str(response.tool_calls[0]["args"]).lower()


@_NO_ANTHROPIC
async def test_anthropic_prompt_cache_read() -> None:
    """TKE2 path: ``CacheAwareChatModel`` + ``cache_stable`` marks the system prefix.

    Claude Haiku 4.5 caches prefixes of at least 4,096 tokens, so the prefix is ~6.4k.
    """
    settings = Settings(
        token_efficiency_enabled=True, prompt_cache_enabled=True, prompt_layout="cache_stable"
    )
    llm = ProviderRegistry(settings=settings).get_llm(model=ANTHROPIC, temperature=0.0)
    system = "You are a terse assistant. " + "Reference policy clause: answers are short. " * 800
    first = await llm.ainvoke([SystemMessage(content=system), HumanMessage(content="Say A.")])
    second = await llm.ainvoke([SystemMessage(content=system), HumanMessage(content="Say B.")])
    u1, u2 = extract_token_usage(first), extract_token_usage(second)
    assert u1.cache_write_tokens + u1.cache_read_tokens > 0
    assert u2.cache_read_tokens > 0


@_NO_OPENAI
def test_openai_invoke() -> None:
    response = _llm(OPENAI).invoke([HumanMessage(content="Say 'pong' and nothing else.")])
    assert isinstance(response.content, str)
    assert "pong" in response.content.lower()
