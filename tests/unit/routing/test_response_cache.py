"""Exact response cache (SPEC-AMR-RSC-001; AMR4-07)."""

from __future__ import annotations

import pytest

from prismal.core.config import Settings
from prismal.routing.response_cache import ResponseCache


class FakeClock:
    def __init__(self, t: float = 1_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "adaptive_routing_enabled": True,
        "response_cache_enabled": True,
        "plan_cache_intents": ["faq"],
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _key(cache: ResponseCache, **overrides: str) -> str:
    base = {
        "org_id": "acme",
        "agent": "researcher",
        "model_ref": "gpt-4o-mini",
        "messages_hash": "abc",
        "prompt_version": "v1",
    }
    base.update(overrides)
    return cache.key(**base)


def test_key_is_sha256_and_deterministic() -> None:
    cache = ResponseCache(settings=_settings())
    k = _key(cache)
    assert k == _key(cache)
    assert len(k) == 64
    int(k, 16)


@pytest.mark.parametrize(
    "field", ["org_id", "agent", "model_ref", "messages_hash", "prompt_version"]
)
def test_key_changes_with_every_field(field: str) -> None:
    cache = ResponseCache(settings=_settings())
    assert _key(cache) != _key(cache, **{field: "other"})


def test_key_fields_are_not_ambiguous() -> None:
    cache = ResponseCache(settings=_settings())
    assert _key(cache, org_id="a|b", agent="c") != _key(cache, org_id="a", agent="b|c")


def test_put_get_roundtrip_and_ttl() -> None:
    clock = FakeClock()
    cache = ResponseCache(settings=_settings(), now=clock)
    k = _key(cache)
    assert cache.get(k) is None
    cache.put(k, "answer", ttl_s=10)
    assert cache.get(k) == "answer"
    clock.t += 11
    assert cache.get(k) is None


def test_lru_bound() -> None:
    clock = FakeClock()
    cache = ResponseCache(settings=_settings(), max_entries=2, now=clock)
    cache.put("a", "1", ttl_s=60)
    cache.put("b", "2", ttl_s=60)
    assert cache.get("a") == "1"
    cache.put("c", "3", ttl_s=60)
    assert cache.get("b") is None
    assert cache.get("a") == "1"
    assert cache.get("c") == "3"


def test_eligible_only_tool_free_deterministic_read_only_untainted() -> None:
    cache = ResponseCache(settings=_settings())
    assert cache.eligible(tools_bound=False, temperature=0.0, intent="faq", tainted=False)


@pytest.mark.parametrize(
    ("overrides", "tools_bound", "temperature", "intent", "tainted"),
    [
        ({"response_cache_enabled": False}, False, 0.0, "faq", False),
        ({"adaptive_routing_enabled": False}, False, 0.0, "faq", False),
        ({}, True, 0.0, "faq", False),
        ({}, False, 0.2, "faq", False),
        ({}, False, 0.0, "write_report", False),
        ({}, False, 0.0, None, False),
        ({}, False, 0.0, "faq", True),
    ],
)
def test_bypass(
    overrides: dict[str, object],
    tools_bound: bool,
    temperature: float,
    intent: str | None,
    tainted: bool,
) -> None:
    cache = ResponseCache(settings=_settings(**overrides))
    assert not cache.eligible(
        tools_bound=tools_bound, temperature=temperature, intent=intent, tainted=tainted
    )
