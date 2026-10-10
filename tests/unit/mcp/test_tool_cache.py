"""Tests for the TTL MCP tool-list cache (SPEC-PRT-MTC-001, PRT3-06)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from prismal.core.config import Settings
from prismal.mcp.protocol import ToolListPage
from prismal.mcp.tool_cache import CachedToolList, McpToolListCache, canonical_tools_hash

pytestmark = pytest.mark.unit


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Metrics:
    def __init__(self) -> None:
        self.counters: list[tuple[str, int, dict[str, Any]]] = []

    def increment_counter(
        self, metric: str, value: int = 1, attributes: dict[str, Any] | None = None
    ) -> None:
        self.counters.append((metric, value, dict(attributes or {})))

    def count(self, metric: str) -> int:
        return sum(v for m, v, _ in self.counters if m == metric)


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "mcp_tool_cache_enabled": True,
        "mcp_tool_cache_default_ttl_s": 300,
        "mcp_tool_cache_max_ttl_s": 600,
        "mcp_tool_cache_max_entries": 3,
    }
    base.update(kw)
    return Settings(**base)


def _page(
    *names: str, ttl_ms: int | None = 60_000, scope: str | None = "shared", desc: str = "d"
) -> ToolListPage:
    tools = tuple(
        {"name": n, "description": desc, "inputSchema": {"type": "object"}} for n in names
    )
    return ToolListPage(tools=tools, ttl_ms=ttl_ms, cache_scope=scope)  # type: ignore[arg-type]


def _cache(
    clock: _Clock | None = None, metrics: _Metrics | None = None, **kw: Any
) -> McpToolListCache:
    return McpToolListCache(settings=_settings(**kw), clock=clock or _Clock(), metrics=metrics)


class TestHash:
    def test_order_independent_and_sorted(self) -> None:
        a = _page("b", "a").tools
        b = _page("a", "b").tools
        assert canonical_tools_hash(a) == canonical_tools_hash(b)

    def test_content_sensitive(self) -> None:
        assert canonical_tools_hash(_page("a").tools) != canonical_tools_hash(
            _page("a", desc="other").tools
        )


class TestPutGet:
    def test_put_returns_sorted_tools_and_get_hits(self) -> None:
        m = _Metrics()
        cache = _cache(metrics=m)
        cached = cache.put("srv", _page("z", "a"), org_id=None, principal=None)
        assert isinstance(cached, CachedToolList)
        assert [t["name"] for t in cached.tools] == ["a", "z"]
        assert cached.scope_key == "srv"
        assert cache.get("srv", org_id="o", principal="p") is cached
        assert m.count("mcp_tool_cache_hits") == 1

    def test_miss_counts(self) -> None:
        m = _Metrics()
        cache = _cache(metrics=m)
        assert cache.get("srv", org_id=None, principal=None) is None
        assert m.count("mcp_tool_cache_misses") == 1

    def test_ttl_expiry(self) -> None:
        clock = _Clock()
        cache = _cache(clock)
        cache.put("srv", _page("a", ttl_ms=10_000), org_id=None, principal=None)
        clock.now += 9.9
        assert cache.get("srv", org_id=None, principal=None) is not None
        clock.now += 0.2
        assert cache.get("srv", org_id=None, principal=None) is None

    def test_ttl_capped_at_max(self) -> None:
        clock = _Clock()
        cache = _cache(clock)
        cached = cache.put("srv", _page("a", ttl_ms=10_000_000), org_id=None, principal=None)
        assert cached.expires_at == pytest.approx(clock.now + 600)

    def test_absent_ttl_uses_default(self) -> None:
        clock = _Clock()
        cache = _cache(clock)
        cached = cache.put("srv", _page("a", ttl_ms=None), org_id=None, principal=None)
        assert cached.expires_at == pytest.approx(clock.now + 300)

    def test_zero_ttl_not_cached(self) -> None:
        cache = _cache()
        cached = cache.put("srv", _page("a", ttl_ms=0), org_id=None, principal=None)
        assert cached.tools
        assert cache.get("srv", org_id=None, principal=None) is None
        assert len(cache) == 0


class TestScope:
    def test_principal_scope_isolated_per_tenant_and_principal(self) -> None:
        cache = _cache()
        mine = cache.put("srv", _page("a", scope="principal"), org_id="o1", principal="u1")
        assert mine.scope_key == "srv:o1:u1"
        assert cache.get("srv", org_id="o1", principal="u1") is mine
        assert cache.get("srv", org_id="o2", principal="u1") is None
        assert cache.get("srv", org_id="o1", principal="u2") is None
        assert cache.get("srv", org_id=None, principal=None) is None

    def test_missing_scope_fails_closed_to_principal(self) -> None:
        cache = _cache()
        cached = cache.put("srv", _page("a", scope=None), org_id="o1", principal="u1")
        assert cached.scope_key == "srv:o1:u1"
        assert cache.get("srv", org_id="o2", principal="u1") is None

    def test_principal_entry_preferred_over_shared(self) -> None:
        cache = _cache()
        cache.put("srv", _page("shared"), org_id=None, principal=None)
        mine = cache.put("srv", _page("mine", scope="principal"), org_id="o", principal="u")
        assert cache.get("srv", org_id="o", principal="u") is mine


class TestStability:
    def test_same_hash_returns_previous_object(self) -> None:
        clock = _Clock()
        m = _Metrics()
        cache = _cache(clock, metrics=m)
        first = cache.put("srv", _page("a", "b"), org_id=None, principal=None)
        clock.now += 1000  # expired
        assert cache.get("srv", org_id=None, principal=None) is None
        second = cache.put("srv", _page("b", "a"), org_id=None, principal=None)
        assert second is first
        # expiry extended: served again
        assert cache.get("srv", org_id=None, principal=None) is first
        assert m.count("mcp_tool_list_changes") == 0

    def test_changed_hash_new_object_and_counted(self) -> None:
        m = _Metrics()
        cache = _cache(metrics=m)
        first = cache.put("srv", _page("a"), org_id=None, principal=None)
        second = cache.put("srv", _page("a", "b"), org_id=None, principal=None)
        assert second is not first
        assert ("mcp_tool_list_changes", 1, {"server": "srv"}) in m.counters

    def test_hundred_rebuilds_byte_identical(self) -> None:
        cache = _cache()
        blocks = set()
        for _ in range(100):
            got = cache.get("srv", org_id=None, principal=None)
            if got is None:
                got = cache.put("srv", _page("x", "y"), org_id=None, principal=None)
            blocks.add(json.dumps(list(got.tools), sort_keys=True))
        assert len(blocks) == 1


class TestLruAndInvalidate:
    def test_lru_bound(self) -> None:
        cache = _cache()
        for i in range(4):
            cache.put(f"s{i}", _page("a"), org_id=None, principal=None)
        assert len(cache) == 3
        assert cache.get("s0", org_id=None, principal=None) is None

    def test_lru_touch_on_get(self) -> None:
        cache = _cache()
        for i in range(3):
            cache.put(f"s{i}", _page("a"), org_id=None, principal=None)
        cache.get("s0", org_id=None, principal=None)
        cache.put("s3", _page("a"), org_id=None, principal=None)
        assert cache.get("s0", org_id=None, principal=None) is not None
        assert cache.get("s1", org_id=None, principal=None) is None

    def test_invalidate_server_and_all(self) -> None:
        cache = _cache()
        cache.put("a", _page("x"), org_id=None, principal=None)
        cache.put("a", _page("x", scope="principal"), org_id="o", principal="u")
        cache.put("b", _page("x"), org_id=None, principal=None)
        assert cache.invalidate("a") == 2
        assert len(cache) == 1
        assert cache.invalidate() == 1
        assert len(cache) == 0

    def test_default_settings_and_metrics(self) -> None:
        cache = McpToolListCache()
        assert cache.get("nope", org_id=None, principal=None) is None
