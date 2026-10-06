"""Exact-key response cache (Phase AMR — SPEC-AMR-RSC-001, SHOULD).

Serves only deterministic, tool-free, read-only, untainted turns: the caller
checks :meth:`ResponseCache.eligible` before :meth:`get` / :meth:`put`. Keys are
tenant-scoped sha256 digests; storage is an in-process LRU with per-entry TTL.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from prismal.core.config import Settings


class ResponseCache:
    """Tenant-scoped exact response cache (LRU + TTL)."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        max_entries: int = 1024,
        now: Callable[[], float] | None = None,
    ) -> None:
        if settings is None:
            from prismal.core.config import get_settings

            settings = get_settings()
        self._settings = settings
        self._max = max(1, max_entries)
        self._now = now or time.time
        self._lock = threading.Lock()
        self._data: OrderedDict[str, tuple[str, float]] = OrderedDict()

    def key(
        self,
        *,
        org_id: str,
        agent: str,
        model_ref: str,
        messages_hash: str,
        prompt_version: str,
    ) -> str:
        """sha256 over an unambiguous (JSON-encoded) tuple of the key fields."""
        payload = json.dumps([org_id, agent, model_ref, messages_hash, prompt_version])
        return hashlib.sha256(payload.encode()).hexdigest()

    def eligible(
        self, *, tools_bound: bool, temperature: float, intent: str | None, tainted: bool
    ) -> bool:
        """Serve only tool-free, temperature-0, read-only-intent, untainted turns."""
        s = self._settings
        return (
            s.adaptive_routing_enabled
            and s.response_cache_enabled
            and not tools_bound
            and temperature == 0
            and intent is not None
            and intent in s.plan_cache_intents
            and not tainted
        )

    def get(self, key: str) -> str | None:
        """Cached content, or ``None`` when absent or expired."""
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            content, expires_at = entry
            if self._now() > expires_at:
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return content

    def put(self, key: str, content: str, *, ttl_s: int) -> None:
        """Store *content* for ``ttl_s`` seconds, evicting the least recently used entry."""
        with self._lock:
            self._data[key] = (content, self._now() + ttl_s)
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)


__all__ = ["ResponseCache"]
