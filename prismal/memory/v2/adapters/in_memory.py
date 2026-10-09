"""Deterministic, I/O-free ``MemoryBackendPort`` (Phase MEM — SPEC-MEM-ADP-001).

Similarity is a token-overlap score in ``[0, 1]``; ordering is stable
(score desc, then record id). Used by tests and by ``memory_v2_backend="memory"``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from prismal.memory.v2.types import ForgetActor, MemoryRecord, MemoryScope, MemoryType

if TYPE_CHECKING:
    from collections.abc import Sequence

_TOKEN = re.compile(r"\w+", re.UNICODE)


def tokens(text: str) -> frozenset[str]:
    """Lower-cased word tokens."""
    return frozenset(t.lower() for t in _TOKEN.findall(text))


def overlap_score(query: str, content: str) -> float:
    """Share of query tokens present in ``content`` (0 when the query has none)."""
    q = tokens(query)
    if not q:
        return 0.0
    return len(q & tokens(content)) / len(q)


class InMemoryBackend:
    """Dict-backed backend; not thread-safe across event loops (tests, dev)."""

    name = "memory"

    def __init__(self) -> None:
        self._records: dict[str, MemoryRecord] = {}

    async def put(self, record: MemoryRecord) -> str:
        self._records[record.id] = record
        return record.id

    async def get(self, record_id: str, *, scope: MemoryScope) -> MemoryRecord | None:
        rec = self._records.get(record_id)
        return rec if rec is not None and scope.covers(rec.scope) else None

    async def search(
        self,
        text: str,
        *,
        scope: MemoryScope,
        types: frozenset[MemoryType] | None,
        k: int,
        include_superseded: bool = False,
    ) -> Sequence[tuple[MemoryRecord, float]]:
        hits: list[tuple[MemoryRecord, float]] = []
        for rec in self._records.values():
            if not scope.covers(rec.scope):
                continue
            if types is not None and rec.type not in types:
                continue
            if rec.superseded_by is not None and not include_superseded:
                continue
            score = overlap_score(text, rec.content)
            if score > 0.0:
                hits.append((rec, score))
        hits.sort(key=lambda h: (-h[1], h[0].id))
        return hits[: max(k, 0)]

    async def update(self, record: MemoryRecord) -> None:
        if record.id not in self._records:
            raise KeyError(record.id)
        self._records[record.id] = record

    async def list(
        self, *, scope: MemoryScope, since: float | None = None, limit: int = 1000
    ) -> Sequence[MemoryRecord]:
        out = [
            r
            for r in sorted(self._records.values(), key=lambda r: (r.provenance.created_at, r.id))
            if scope.covers(r.scope) and (since is None or r.provenance.created_at > since)
        ]
        return out[: max(limit, 0)]

    async def purge(
        self, record_ids: Sequence[str], *, scope: MemoryScope, _authorized: ForgetActor
    ) -> int:
        if not isinstance(_authorized, ForgetActor):
            raise TypeError("purge requires a ForgetActor (only ForgetPolicy may call it)")
        removed = 0
        for rid in record_ids:
            rec = self._records.get(rid)
            if rec is not None and scope.covers(rec.scope):
                del self._records[rid]
                removed += 1
        return removed


__all__ = ["InMemoryBackend", "overlap_score", "tokens"]
