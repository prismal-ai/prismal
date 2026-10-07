"""TTL tool-list cache with identity-preserving refresh (SPEC-PRT-MTC-001).

* TTL = ``min(ttl_ms / 1000, mcp_tool_cache_max_ttl_s)``; no ``ttlMs`` ⇒
  ``mcp_tool_cache_default_ttl_s``; ``ttlMs == 0`` ⇒ never stored.
* Scope: ``shared`` lists are keyed by ``server``; ``principal`` lists (and lists without a
  ``cacheScope`` — fail closed) by ``server:org:principal`` and are never served across
  tenants or principals.
* Stability rule (feeds TKE2/TKE3 prompt caching): when a refresh has the same content hash as
  the stored list, the *previous* :class:`CachedToolList` object is returned, so the tool block an
  agent observes changes only when the TTL expired **and** the content changed. The entry's
  authoritative expiry lives in the cache (the frozen object keeps its creation-time expiry).
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from prismal.mcp.protocol import MetricsSink, ToolListPage, default_metrics

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from prismal.core.config import Settings

try:  # xxhash64 when importable; blake2b (stdlib) is the fallback.
    import xxhash as _xxhash

    _HAS_XXHASH = True
except ImportError:  # pragma: no cover - environment without xxhash
    _HAS_XXHASH = False


@dataclass(frozen=True)
class CachedToolList:
    """An immutable, canonically ordered tool list for one scope."""

    server: str
    scope_key: str
    tools: tuple[Mapping[str, Any], ...]
    content_hash: str
    fetched_at: float
    expires_at: float


@dataclass
class _Entry:
    cached: CachedToolList
    expires_at: float


def _sorted_tools(tools: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    return tuple(sorted(tools, key=lambda t: str(t.get("name", ""))))


def canonical_tools_hash(tools: Sequence[Mapping[str, Any]]) -> str:
    """Hash of the tools sorted by name, serialized as sorted-key compact JSON."""
    payload = json.dumps(
        [dict(t) for t in _sorted_tools(tools)],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    if _HAS_XXHASH:
        return str(_xxhash.xxh64(payload).hexdigest())
    return hashlib.blake2b(payload, digest_size=8).hexdigest()  # pragma: no cover


def principal_scope_key(server: str, org_id: str | None, principal: str | None) -> str:
    """``server:org:principal`` (``-`` stands for an absent component)."""
    return f"{server}:{org_id or '-'}:{principal or '-'}"


class McpToolListCache:
    """Bounded LRU of :class:`CachedToolList` keyed by scope."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        clock: Callable[[], float] = time.monotonic,
        metrics: MetricsSink | None = None,
    ) -> None:
        if settings is None:
            from prismal.core.config import get_settings

            settings = get_settings()
        self._default_ttl = float(settings.mcp_tool_cache_default_ttl_s)
        self._max_ttl = float(settings.mcp_tool_cache_max_ttl_s)
        self._max_entries = int(settings.mcp_tool_cache_max_entries)
        self._clock = clock
        self._metrics = metrics if metrics is not None else default_metrics()
        self._entries: OrderedDict[str, _Entry] = OrderedDict()

    def __len__(self) -> int:
        return len(self._entries)

    def _keys_for(self, server: str, org_id: str | None, principal: str | None) -> tuple[str, str]:
        return principal_scope_key(server, org_id, principal), server

    def get(
        self, server: str, *, org_id: str | None, principal: str | None
    ) -> CachedToolList | None:
        """Return the live entry for this scope (principal first, then shared) or ``None``."""
        now = self._clock()
        for key in self._keys_for(server, org_id, principal):
            entry = self._entries.get(key)
            if entry is not None and entry.expires_at > now:
                self._entries.move_to_end(key)
                self._metrics.increment_counter("mcp_tool_cache_hits")
                return entry.cached
        self._metrics.increment_counter("mcp_tool_cache_misses")
        return None

    def put(
        self,
        server: str,
        page: ToolListPage,
        *,
        org_id: str | None,
        principal: str | None,
    ) -> CachedToolList:
        """Store a fresh page; same content hash ⇒ the previous object is returned."""
        now = self._clock()
        key = (
            server
            if page.cache_scope == "shared"
            else principal_scope_key(server, org_id, principal)
        )
        ttl = self._default_ttl if page.ttl_ms is None else min(page.ttl_ms / 1000, self._max_ttl)
        expires_at = now + max(ttl, 0.0)
        content_hash = canonical_tools_hash(page.tools)

        previous = self._entries.get(key)
        if previous is not None and previous.cached.content_hash == content_hash:
            cached = previous.cached
        else:
            if previous is not None:
                self._metrics.increment_counter(
                    "mcp_tool_list_changes", attributes={"server": server}
                )
            cached = CachedToolList(
                server=server,
                scope_key=key,
                tools=_sorted_tools(page.tools),
                content_hash=content_hash,
                fetched_at=now,
                expires_at=expires_at,
            )

        if ttl <= 0:
            self._entries.pop(key, None)
            return cached
        self._entries[key] = _Entry(cached=cached, expires_at=expires_at)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
        return cached

    def invalidate(self, server: str | None = None) -> int:
        """Drop every entry (``server=None``) or every scope of *server*; return the count."""
        if server is None:
            count = len(self._entries)
            self._entries.clear()
            return count
        doomed = [k for k, e in self._entries.items() if e.cached.server == server]
        for key in doomed:
            del self._entries[key]
        return len(doomed)


__all__ = [
    "CachedToolList",
    "McpToolListCache",
    "canonical_tools_hash",
    "principal_scope_key",
]
