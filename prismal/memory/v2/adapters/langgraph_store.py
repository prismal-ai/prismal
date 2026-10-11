"""``MemoryBackendPort`` over the legacy ``LongTermMemoryStore`` (Phase MEM — SPEC-MEM-ADP-001).

The legacy class is wrapped, never edited; its LangGraph ``BaseStore`` is reused.
v2 records live under the tenant namespace ``("prismal", org_id, user_id or "_",
type)`` (SPEC-MEM-TEN-001). Each stored document keeps the legacy shape
(``value`` / ``user_id`` / ``created_at`` / ``expires_at``, value PII-sanitized
exactly as ``store_fact`` does) plus the ``mv2_*`` fields, so the unchanged
``recall_facts()`` returns v2-written facts.

Legacy facts stored under ``legacy_namespaces`` (e.g. ``("preferences",)``) are
read too. Un-annotated ones carry no tenant: they are attributed to
``legacy_org_id`` and are invisible to every other org — and to all orgs when it is
``None`` (fail-closed). Their record id encodes the legacy namespace and key
(``legacy:[...]``) so ``get`` / ``purge`` can address them.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from prismal.core.exceptions import MemoryScanLimitError
from prismal.memory.v2.adapters.in_memory import overlap_score
from prismal.memory.v2.migration import (
    LegacyEntry,
    has_mv2,
    legacy_record,
    record_from_mv2,
    record_to_mv2,
)
from prismal.memory.v2.types import ForgetActor, MemoryRecord, MemoryScope, MemoryType
from prismal.security.pii_sanitizer import PIISanitizer

if TYPE_CHECKING:
    import builtins
    from collections.abc import Iterable, Sequence

    from langgraph.store.base import BaseStore

    from prismal.core.config import Settings
    from prismal.memory.long_term_store import LongTermMemoryStore

_ROOT = "prismal"
_LEGACY_PREFIX = "legacy:"
_NO_USER = "_"

Address = tuple[tuple[str, ...], str]


def encode_legacy_id(namespace: tuple[str, ...], key: str) -> str:
    """Stable, reversible record id for a legacy fact."""
    return _LEGACY_PREFIX + json.dumps([list(namespace), key], separators=(",", ":"))


def decode_legacy_id(record_id: str) -> Address | None:
    """Inverse of :func:`encode_legacy_id`; ``None`` for anything else."""
    if not record_id.startswith(_LEGACY_PREFIX):
        return None
    try:
        ns, key = json.loads(record_id[len(_LEGACY_PREFIX) :])
    except (ValueError, TypeError):
        return None
    if not isinstance(ns, list) or not isinstance(key, str):
        return None
    return tuple(str(p) for p in ns), key


class LangGraphStoreBackend:
    """v2 storage in the legacy LangGraph long-term store, tenant-namespaced.

    Args:
        store: The legacy instance to wrap (its ``BaseStore`` is reused).
        settings: Accepted for constructor symmetry with the other adapters.
        legacy_namespaces: Namespace prefixes holding legacy facts to expose/backfill.
        legacy_org_id: Tenant that owns un-annotated legacy facts; ``None`` hides them.
        page_size: ``asearch`` page size when scanning.
        max_scan: Optional cap on items scanned per namespace prefix and call
            (``None`` = unbounded). Hitting it raises ``MemoryScanLimitError``
            rather than truncating: erasure and retention list through this scan.
    """

    name = "langgraph_store"

    def __init__(
        self,
        store: LongTermMemoryStore,
        *,
        settings: Settings | None = None,
        legacy_namespaces: Iterable[tuple[str, ...]] = (),
        legacy_org_id: str | None = None,
        page_size: int = 200,
        max_scan: int | None = None,
    ) -> None:
        self._settings = settings
        self._store: BaseStore = store._store
        self._legacy_ns = tuple(tuple(ns) for ns in legacy_namespaces)
        if any(ns and ns[0] == _ROOT for ns in self._legacy_ns):
            raise ValueError(f"legacy namespaces must not live under the '{_ROOT}' root")
        self._legacy_org = legacy_org_id
        self._page = max(page_size, 1)
        self._max_scan = None if max_scan is None else max(max_scan, 1)

    @staticmethod
    def namespace(scope: MemoryScope, mtype: MemoryType) -> tuple[str, str, str, str]:
        """Tenant namespace ``("prismal", org_id, user_id or "_", type)``."""
        return (_ROOT, scope.org_id, scope.user_id or _NO_USER, mtype.value)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _to_record(self, namespace: tuple[str, ...], key: str, value: Any) -> MemoryRecord | None:
        if not isinstance(value, dict):
            return None
        content = str(value.get("value", ""))
        if has_mv2(value):
            return record_from_mv2(value, content=content)
        if self._legacy_org is None or namespace[:1] == (_ROOT,):
            return None
        created = value.get("created_at")
        return legacy_record(
            record_id=encode_legacy_id(namespace, key),
            content=content,
            org_id=self._legacy_org,
            user_id=str(value.get("user_id") or "") or None,
            session_id=None,
            created_at=float(created) if isinstance(created, int | float) else 0.0,
            expires_at=value.get("expires_at"),
        )

    async def _items(
        self, prefixes: Iterable[tuple[str, ...]]
    ) -> builtins.list[tuple[Address, Any]]:
        out: list[tuple[Address, Any]] = []
        for prefix in prefixes:
            offset = 0
            while True:
                page = await self._store.asearch(prefix, limit=self._page, offset=offset)
                offset += len(page)
                if self._max_scan is not None and offset > self._max_scan:
                    raise MemoryScanLimitError(
                        f"memory scan exceeded max_scan={self._max_scan}; refusing a partial result"
                    )
                out.extend(((tuple(i.namespace), i.key), i.value) for i in page)
                if len(page) < self._page:
                    break
        return out

    async def _visible(self, scope: MemoryScope) -> builtins.list[MemoryRecord]:
        out: list[MemoryRecord] = []
        for addr, value in await self._items(((_ROOT, scope.org_id), *self._legacy_ns)):
            rec = self._to_record(addr[0], addr[1], value)
            if rec is not None and scope.covers(rec.scope):
                out.append(rec)
        return out

    async def _locate(
        self, record_id: str, scope: MemoryScope
    ) -> tuple[Address, MemoryRecord] | None:
        legacy_addr = decode_legacy_id(record_id)
        if legacy_addr is not None:
            ns, key = legacy_addr
            if not any(ns[: len(p)] == p for p in self._legacy_ns):
                return None
            item = await self._store.aget(ns, key)
            rec = self._to_record(ns, key, item.value) if item is not None else None
        else:
            hits = await self._store.asearch(
                (_ROOT, scope.org_id), filter={"mv2_id": record_id}, limit=1
            )
            if not hits:
                return None
            ns, key = tuple(hits[0].namespace), hits[0].key
            rec = self._to_record(ns, key, hits[0].value)
        if rec is None or not scope.covers(rec.scope):
            return None
        return (ns, key), rec

    def _target(self, record: MemoryRecord) -> Address:
        legacy_addr = decode_legacy_id(record.id)
        if legacy_addr is not None:
            return legacy_addr
        return self.namespace(record.scope, record.type), record.id

    def _document(self, record: MemoryRecord) -> dict[str, Any]:
        return {
            "value": PIISanitizer.sanitize(record.content),
            "user_id": record.scope.user_id or "",
            "created_at": record.provenance.created_at,
            "expires_at": record.expires_at,
            **record_to_mv2(record),
        }

    async def _write(self, record: MemoryRecord, previous: Address | None) -> str:
        target = self._target(record)
        if previous is not None and previous != target:
            await self._store.adelete(*previous)
        await self._store.aput(target[0], target[1], self._document(record))
        return record.id

    # ── MemoryBackendPort ────────────────────────────────────────────────────

    async def put(self, record: MemoryRecord) -> str:
        found = await self._locate(record.id, record.scope)
        return await self._write(record, found[0] if found else None)

    async def get(self, record_id: str, *, scope: MemoryScope) -> MemoryRecord | None:
        found = await self._locate(record_id, scope)
        return found[1] if found else None

    async def search(
        self,
        text: str,
        *,
        scope: MemoryScope,
        types: frozenset[MemoryType] | None,
        k: int,
        include_superseded: bool = False,
    ) -> Sequence[tuple[MemoryRecord, float]]:
        if k <= 0:
            return []
        now = time.time()
        hits: list[tuple[MemoryRecord, float]] = []
        for rec in await self._visible(scope):
            if types is not None and rec.type not in types:
                continue
            if rec.superseded_by is not None and not include_superseded:
                continue
            if rec.expires_at is not None and rec.expires_at < now:
                continue
            score = overlap_score(text, rec.content)
            if score > 0.0:
                hits.append((rec, score))
        hits.sort(key=lambda h: (-h[1], h[0].id))
        return hits[:k]

    async def update(self, record: MemoryRecord) -> None:
        found = await self._locate(record.id, record.scope)
        if found is None:
            raise KeyError(record.id)
        await self._write(record, found[0])

    async def list(
        self, *, scope: MemoryScope, since: float | None = None, limit: int = 1000
    ) -> Sequence[MemoryRecord]:
        out = [
            rec
            for rec in await self._visible(scope)
            if since is None or rec.provenance.created_at > since
        ]
        out.sort(key=lambda r: (r.provenance.created_at, r.id))
        return out[: max(limit, 0)]

    async def purge(
        self, record_ids: Sequence[str], *, scope: MemoryScope, _authorized: ForgetActor
    ) -> int:
        if not isinstance(_authorized, ForgetActor):
            raise TypeError("purge requires a ForgetActor (only ForgetPolicy may call it)")
        removed = 0
        for rid in dict.fromkeys(record_ids):
            found = await self._locate(rid, scope)
            if found is not None:
                await self._store.adelete(*found[0])
                removed += 1
        return removed

    # ── legacy backfill (LegacyAnnotatable) ──────────────────────────────────

    async def scan_legacy(
        self, *, scope: MemoryScope, limit: int, offset: int
    ) -> Sequence[LegacyEntry]:
        """Facts under ``legacy_namespaces`` visible to ``scope``, in stable id order."""
        entries: list[LegacyEntry] = []
        for addr, value in await self._items(self._legacy_ns):
            rec = self._to_record(addr[0], addr[1], value)
            if rec is not None and scope.covers(rec.scope):
                entries.append(LegacyEntry(record=rec, annotated=has_mv2(value)))
        entries.sort(key=lambda e: e.record.id)
        return entries[offset : offset + limit]

    async def annotate(self, record: MemoryRecord) -> bool:
        """Merge ``mv2_*`` keys into an un-annotated legacy fact; its value stays as is."""
        addr = decode_legacy_id(record.id)
        if addr is None:
            return False
        item = await self._store.aget(*addr)
        if item is None or not isinstance(item.value, dict) or has_mv2(item.value):
            return False
        await self._store.aput(addr[0], addr[1], {**item.value, **record_to_mv2(record)})
        return True


__all__ = ["LangGraphStoreBackend", "decode_legacy_id", "encode_legacy_id"]
