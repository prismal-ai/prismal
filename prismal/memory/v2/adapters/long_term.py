"""``MemoryBackendPort`` over the legacy ``LongTermMemory`` (Phase MEM — SPEC-MEM-ADP-001).

The legacy class is wrapped, never edited. A v2 record is written into the same
two stores legacy uses — the ``memory_entries`` SQLite row (id, session, redacted
content, timestamps) and a vector document carrying the legacy metadata keys
(``entry_id`` / ``session_id`` / ``source``) — so the unchanged
``LongTermMemory.recall()`` returns it.

The legacy SQLite row has no metadata column, so the ``mv2_*`` fields live in a
companion table, ``memory_v2_meta``, in the same database file (the legacy table
is untouched). That table is authoritative for v2 reads; the vector document also
carries ``mv2_org`` / ``mv2_type`` / ``mv2_version`` as a write-time copy.

Tenancy (SPEC-MEM-TEN-001): every read filters with ``MemoryScope.covers``.
Un-annotated legacy rows have no tenant; they are attributed to ``legacy_org_id``
and are invisible to every other org — and to all orgs when it is ``None``
(fail-closed).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from langchain_core.documents import Document

from prismal.memory.long_term import _redact_sensitive
from prismal.memory.v2.migration import (
    LegacyEntry,
    legacy_record,
    record_from_mv2,
    record_to_mv2,
)
from prismal.memory.v2.types import ForgetActor, MemoryRecord, MemoryScope, MemoryType

if TYPE_CHECKING:
    import builtins
    from collections.abc import Sequence
    from pathlib import Path

    from prismal.core.config import Settings
    from prismal.memory.long_term import LongTermMemory

_DT_FMT = "%Y-%m-%dT%H:%M:%S"  # legacy memory_entries timestamp format
# Legacy ``expires_at`` written for v2 rows without their own expiry: retention of v2
# records is ForgetPolicy's job, so the legacy TTL sweep (``expire()``) must never
# reach them (it would delete unaudited and orphan the meta row).
_NEVER_EXPIRES = "9999-12-31T23:59:59"
_META_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_v2_meta (
    id      TEXT PRIMARY KEY,
    org_id  TEXT NOT NULL,
    meta    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_v2_meta_org ON memory_v2_meta (org_id);
"""
_SELECT = (
    "SELECT e.id, e.session_id, e.content, e.created_at, e.expires_at, m.meta"
    " FROM memory_entries e LEFT JOIN memory_v2_meta m ON m.id = e.id"
)
_TENANT_WHERE = " WHERE (m.org_id = ? OR (m.id IS NULL AND ? = 1))"
_ORDER = " ORDER BY e.created_at, e.id"


def _ts_to_str(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).replace(tzinfo=None).strftime(_DT_FMT)


def _str_to_ts(value: str) -> float:
    return datetime.strptime(value, _DT_FMT).replace(tzinfo=UTC).timestamp()


def _now() -> float:
    return datetime.now(UTC).timestamp()


class LongTermMemoryBackend:
    """v2 storage in the legacy ``LongTermMemory`` stores (SQLite + vector store).

    Args:
        ltm: The legacy instance to wrap (its SQLite path and vector store are reused).
        settings: Accepted for constructor symmetry with the other adapters.
        legacy_org_id: Tenant that owns un-annotated legacy rows; ``None`` hides them.
        overfetch: Vector candidates fetched per requested hit, before tenant filtering.
    """

    name = "legacy_long_term"

    def __init__(
        self,
        ltm: LongTermMemory,
        *,
        settings: Settings | None = None,
        legacy_org_id: str | None = None,
        overfetch: int = 4,
    ) -> None:
        self._ltm = ltm
        self._settings = settings
        self._db: Path = ltm._db
        self._vectors = ltm._store
        self._legacy_org = legacy_org_id
        self._overfetch = max(overfetch, 1)
        with closing(self._connect()) as conn, conn:
            conn.executescript(_META_SCHEMA)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db)
        conn.row_factory = sqlite3.Row
        return conn

    def _legacy_visible(self, scope: MemoryScope) -> int:
        return 1 if self._legacy_org is not None and self._legacy_org == scope.org_id else 0

    def _row_to_record(self, row: sqlite3.Row) -> MemoryRecord | None:
        if row["meta"] is not None:
            meta: dict[str, Any] = json.loads(row["meta"])
            return record_from_mv2(meta, content=row["content"])
        if self._legacy_org is None:
            return None
        return legacy_record(
            record_id=row["id"],
            content=row["content"],
            org_id=self._legacy_org,
            user_id=None,
            session_id=row["session_id"],
            created_at=_str_to_ts(row["created_at"]),
            expires_at=_str_to_ts(row["expires_at"]),
        )

    def _fetch(self, record_id: str, scope: MemoryScope) -> MemoryRecord | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                _SELECT + _TENANT_WHERE + " AND e.id = ?",
                (scope.org_id, self._legacy_visible(scope), record_id),
            ).fetchone()
        if row is None:
            return None
        rec = self._row_to_record(row)
        return rec if rec is not None and scope.covers(rec.scope) else None

    def _index(self, record_id: str, content: str, record: MemoryRecord) -> None:
        self._vectors.add_documents(
            [
                Document(
                    page_content=content,
                    metadata={
                        "entry_id": record_id,
                        "session_id": record.provenance.session_id or "",
                        "source": record_id,
                        "mv2_org": record.scope.org_id,
                        "mv2_type": record.type.value,
                        "mv2_version": record_to_mv2(record)["mv2_version"],
                    },
                )
            ]
        )

    def _write(self, record: MemoryRecord) -> str:
        content = _redact_sensitive(record.content)
        created = record.provenance.created_at
        expires = _ts_to_str(record.expires_at) if record.expires_at is not None else _NEVER_EXPIRES
        with closing(self._connect()) as conn, conn:
            existed = (
                conn.execute("SELECT 1 FROM memory_entries WHERE id = ?", (record.id,)).fetchone()
                is not None
            )
            conn.execute(
                "INSERT OR REPLACE INTO memory_entries"
                " (id, session_id, content, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                (
                    record.id,
                    record.provenance.session_id or "",
                    content,
                    _ts_to_str(created),
                    expires,
                ),
            )
            conn.execute(
                "INSERT OR REPLACE INTO memory_v2_meta (id, org_id, meta) VALUES (?, ?, ?)",
                (record.id, record.scope.org_id, json.dumps(record_to_mv2(record))),
            )
        if existed:
            self._vectors.delete_by_source(record.id)
        self._index(record.id, content, record)
        return record.id

    def _scan(self, scope: MemoryScope) -> builtins.list[MemoryRecord]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                _SELECT + _TENANT_WHERE + _ORDER,
                (scope.org_id, self._legacy_visible(scope)),
            ).fetchall()
        return [r for r in (self._row_to_record(row) for row in rows) if r is not None]

    # ── MemoryBackendPort ────────────────────────────────────────────────────

    async def put(self, record: MemoryRecord) -> str:
        return self._write(record)

    async def get(self, record_id: str, *, scope: MemoryScope) -> MemoryRecord | None:
        return self._fetch(record_id, scope)

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
        now = _now()
        hits: dict[str, tuple[MemoryRecord, float]] = {}
        for doc, score in self._vectors.similarity_search(text, k=k * self._overfetch):
            entry_id = doc.metadata.get("entry_id")
            if not isinstance(entry_id, str) or entry_id in hits:
                continue
            rec = self._fetch(entry_id, scope)
            if rec is None:
                continue
            if types is not None and rec.type not in types:
                continue
            if rec.superseded_by is not None and not include_superseded:
                continue
            if rec.expires_at is not None and rec.expires_at < now:
                continue
            hits[entry_id] = (rec, min(max(float(score), 0.0), 1.0))
        ranked = sorted(hits.values(), key=lambda h: (-h[1], h[0].id))
        return ranked[:k]

    async def update(self, record: MemoryRecord) -> None:
        if self._fetch(record.id, record.scope) is None:
            raise KeyError(record.id)
        self._write(record)

    async def list(
        self, *, scope: MemoryScope, since: float | None = None, limit: int = 1000
    ) -> Sequence[MemoryRecord]:
        out = [
            r
            for r in self._scan(scope)
            if scope.covers(r.scope) and (since is None or r.provenance.created_at > since)
        ]
        out.sort(key=lambda r: (r.provenance.created_at, r.id))
        return out[: max(limit, 0)]

    async def purge(
        self, record_ids: Sequence[str], *, scope: MemoryScope, _authorized: ForgetActor
    ) -> int:
        if not isinstance(_authorized, ForgetActor):
            raise TypeError("purge requires a ForgetActor (only ForgetPolicy may call it)")
        targets = [rid for rid in dict.fromkeys(record_ids) if self._fetch(rid, scope)]
        with closing(self._connect()) as conn, conn:
            for rid in targets:
                conn.execute("DELETE FROM memory_entries WHERE id = ?", (rid,))
                conn.execute("DELETE FROM memory_v2_meta WHERE id = ?", (rid,))
        for rid in targets:
            self._vectors.delete_by_source(rid)
        return len(targets)

    # ── legacy backfill (LegacyAnnotatable) ──────────────────────────────────

    async def scan_legacy(
        self, *, scope: MemoryScope, limit: int, offset: int
    ) -> Sequence[LegacyEntry]:
        """Rows of the legacy table visible to ``scope`` (v2-native ones come back annotated)."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                _SELECT + _TENANT_WHERE + _ORDER + " LIMIT ? OFFSET ?",
                (scope.org_id, self._legacy_visible(scope), limit, offset),
            ).fetchall()
        pairs = [(self._row_to_record(row), row["meta"] is not None) for row in rows]
        return [LegacyEntry(record=r, annotated=a) for r, a in pairs if r is not None]

    async def annotate(self, record: MemoryRecord) -> bool:
        """Add the ``mv2_*`` row for an existing, un-annotated legacy entry (never overwrites)."""
        with closing(self._connect()) as conn, conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO memory_v2_meta (id, org_id, meta)"
                " SELECT ?, ?, ? WHERE EXISTS (SELECT 1 FROM memory_entries WHERE id = ?)",
                (record.id, record.scope.org_id, json.dumps(record_to_mv2(record)), record.id),
            )
            return cur.rowcount == 1


__all__ = ["LongTermMemoryBackend"]
