"""Cache/reasoning fields on TokenCounts and Usage (Phase TKE — SPEC-TKE-ACC-001)."""

from __future__ import annotations

from prismal.budget.types import TokenCounts, Usage


def test_token_counts_new_fields_default_to_zero() -> None:
    t = TokenCounts(10, 5)
    assert (t.cache_read_tokens, t.cache_write_tokens, t.reasoning_tokens) == (0, 0, 0)
    assert t.total == 15


def test_uncached_prompt_tokens() -> None:
    t = TokenCounts(prompt_tokens=1000, cache_read_tokens=700, cache_write_tokens=200)
    assert t.uncached_prompt_tokens == 100


def test_uncached_prompt_tokens_never_negative() -> None:
    assert TokenCounts(prompt_tokens=10, cache_read_tokens=50).uncached_prompt_tokens == 0


def test_normalized_converts_anthropic_raw_semantics() -> None:
    raw = TokenCounts(prompt_tokens=100, cache_read_tokens=800, cache_write_tokens=50)
    assert raw.normalized().prompt_tokens == 950


def test_normalized_is_identity_for_openai_semantics() -> None:
    t = TokenCounts(prompt_tokens=1000, cache_read_tokens=800)
    assert t.normalized() == t


def test_usage_new_fields_default() -> None:
    u = Usage()
    assert (u.cache_read_tokens, u.cache_write_tokens, u.reasoning_tokens) == (0, 0, 0)
    assert u.cost_source == ""
    assert u.cache_hit_ratio == 0.0


def test_usage_add_sums_new_fields_and_keeps_total() -> None:
    a = Usage(
        prompt_tokens=100,
        completion_tokens=10,
        cache_read_tokens=80,
        reasoning_tokens=3,
        cost_source="litellm",
    )
    b = Usage(prompt_tokens=50, completion_tokens=5, cache_write_tokens=20, cost_source="")
    c = a + b
    assert c.cache_read_tokens == 80
    assert c.cache_write_tokens == 20
    assert c.reasoning_tokens == 3
    assert c.total_tokens == c.prompt_tokens + c.completion_tokens == 165
    assert c.cost_source == "litellm"


def test_usage_add_keeps_last_non_empty_source() -> None:
    c = Usage(cost_source="table") + Usage(cost_source="gateway")
    assert c.cost_source == "gateway"


def test_cache_hit_ratio() -> None:
    assert Usage(prompt_tokens=1000, cache_read_tokens=600).cache_hit_ratio == 0.6
