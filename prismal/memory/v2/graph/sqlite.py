"""SQLite temporal memory graph (Phase MEM — SPEC-MEM-GRF-001, MEM6-01).

Stdlib ``sqlite3`` only (no extra). Edges carry ``valid_from``/``valid_to``;
:meth:`SqliteMemoryGraph.invalidate_edge` closes an edge and never deletes it,
and :meth:`SqliteMemoryGraph.neighbors` answers point-in-time (``as_of``)
queries. Every statement is filtered by ``org_id`` and every returned edge is
re-checked with :meth:`MemoryScope.covers` against the stored node / edge
scopes (SPEC-MEM-TEN-001).

The single exception to "never delete" is erasure:
:meth:`SqliteMemoryGraph.purge_nodes` removes nodes and their incident edges
and is meant to be called only by ``ForgetPolicy`` with ``include_derived``
(SPEC-MEM-DEL-001). Nodes store a content hash, never the content itself.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from typing import TYPE_CHECKING

from prismal.memory.v2.types import (
    ForgetActor,
    MemoryEdge,
    MemoryScope,
    RelationKind,
    within_erasure_scope,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from prismal.memory.v2.types import MemoryRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mv2_nodes (
    org_id TEXT NOT NULL,
    id TEXT NOT NULL,
    user_id TEXT,
    agent TEXT,
    thread_id TEXT,
    type TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    tainted INTEGER NOT NULL,
    PRIMARY KEY (org_id, id)
);
CREATE TABLE IF NOT EXISTS mv2_edges (
    org_id TEXT NOT NULL,
    user_id TEXT,
    agent TEXT,
    thread_id TEXT,
    src_id TEXT NOT NULL,
    dst_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    confidence REAL NOT NULL,
    valid_from REAL NOT NULL,
    valid_to REAL
);
CREATE INDEX IF NOT EXISTS mv2_edges_src ON mv2_edges (org_id, src_id);
CREATE INDEX IF NOT EXISTS mv2_edges_dst ON mv2_edges (org_id, dst_id);
"""

_EdgeRow = tuple[str | None, str | None, str | None, str, str, str, float, float, float | None]


class SqliteMemoryGraph:
    """``MemoryGraphPort`` over stdlib sqlite3 with temporal, tenant-scoped edges."""

    name = "sqlite"

    def __init__(self, path: str | Path = ":memory:") -> None:
        """Open (and create) the graph database at *path* (default in-memory)."""
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ── MemoryGraphPort ───────────────────────────────────────────────────────

    async def upsert_node(self, record: MemoryRecord) -> None:
        """Insert or refresh the node for *record* inside its own tenant."""
        scope = record.scope
        digest = hashlib.sha256(record.content.encode("utf-8")).hexdigest()
        with self._lock:
            self._conn.execute(
                "INSERT INTO mv2_nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (org_id, id) DO UPDATE SET user_id=excluded.user_id, "
                "agent=excluded.agent, thread_id=excluded.thread_id, type=excluded.type, "
                "content_sha256=excluded.content_sha256, tainted=excluded.tainted",
                (
                    scope.org_id,
                    record.id,
                    scope.user_id,
                    scope.agent,
                    scope.thread_id,
                    str(record.type),
                    digest,
                    int(record.provenance.tainted),
                ),
            )
            self._conn.commit()

    async def add_edge(self, edge: MemoryEdge, *, scope: MemoryScope) -> None:
        """Store *edge* under *scope* (append-only)."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO mv2_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    scope.org_id,
                    scope.user_id,
                    scope.agent,
                    scope.thread_id,
                    edge.src_id,
                    edge.dst_id,
                    str(edge.relation),
                    edge.confidence,
                    edge.valid_from,
                    edge.valid_to,
                ),
            )
            self._conn.commit()

    async def invalidate_edge(
        self, src_id: str, dst_id: str, *, at: float, scope: MemoryScope
    ) -> None:
        """Close every open ``src → dst`` edge of the tenant at time *at* (never deletes)."""
        with self._lock:
            self._conn.execute(
                "UPDATE mv2_edges SET valid_to = ? WHERE org_id = ? AND src_id = ? "
                "AND dst_id = ? AND valid_to IS NULL",
                (at, scope.org_id, src_id, dst_id),
            )
            self._conn.commit()

    async def neighbors(
        self,
        record_id: str,
        *,
        scope: MemoryScope,
        as_of: float | None,
        relations: frozenset[RelationKind] | None = None,
    ) -> Sequence[MemoryEdge]:
        """Edges touching *record_id* that are valid now (``as_of=None``) or at ``as_of``."""
        with self._lock:
            if not self._node_visible(record_id, scope):
                return []
            sql = (
                "SELECT user_id, agent, thread_id, src_id, dst_id, relation, confidence, "
                "valid_from, valid_to FROM mv2_edges WHERE org_id = ? "
                "AND (src_id = ? OR dst_id = ?)"
            )
            params: list[object] = [scope.org_id, record_id, record_id]
            if as_of is None:
                sql += " AND valid_to IS NULL"
            else:
                sql += " AND valid_from <= ? AND (valid_to IS NULL OR valid_to > ?)"
                params.extend([as_of, as_of])
            rows: list[_EdgeRow] = self._conn.execute(sql + " ORDER BY rowid", params).fetchall()
            out: list[MemoryEdge] = []
            for user_id, agent, thread_id, src, dst, rel, conf, vfrom, vto in rows:
                relation = RelationKind(rel)
                if relations is not None and relation not in relations:
                    continue
                edge_scope = MemoryScope(
                    org_id=scope.org_id, user_id=user_id, agent=agent, thread_id=thread_id
                )
                other = dst if src == record_id else src
                if not scope.covers(edge_scope) or not self._node_visible(other, scope):
                    continue
                out.append(
                    MemoryEdge(
                        src_id=src,
                        dst_id=dst,
                        relation=relation,
                        confidence=conf,
                        valid_from=vfrom,
                        valid_to=vto,
                    )
                )
            return out

    # ── erasure hook (ForgetPolicy only) ─────────────────────────────────────

    async def purge_nodes(
        self, record_ids: Sequence[str], *, scope: MemoryScope, _authorized: ForgetActor
    ) -> int:
        """Erase the nodes *record_ids* and every incident edge in the tenant.

        The only deleting operation of the graph; reserved for ``ForgetPolicy``
        (``include_derived`` erasure completeness). Returns the nodes removed.
        """
        if not isinstance(_authorized, ForgetActor):
            raise TypeError("purge_nodes requires a ForgetActor")
        with self._lock:
            ids = [rid for rid in dict.fromkeys(record_ids) if self._erasable(rid, scope)]
            if not ids:
                return 0
            marks = ",".join("?" for _ in ids)
            self._conn.execute(
                f"DELETE FROM mv2_edges WHERE org_id = ? AND "  # noqa: S608 - placeholders only
                f"(src_id IN ({marks}) OR dst_id IN ({marks}))",
                [scope.org_id, *ids, *ids],
            )
            cur = self._conn.execute(
                f"DELETE FROM mv2_nodes WHERE org_id = ? AND id IN ({marks})",  # noqa: S608
                [scope.org_id, *ids],
            )
            self._conn.commit()
            return cur.rowcount

    # ── introspection / lifecycle ─────────────────────────────────────────────

    def edge_count(self, *, scope: MemoryScope) -> int:
        """Number of stored edges (open or invalidated) of the tenant."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM mv2_edges WHERE org_id = ?", (scope.org_id,)
            ).fetchone()
        return int(row[0])

    def node_count(self, *, scope: MemoryScope) -> int:
        """Number of stored nodes of the tenant."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM mv2_nodes WHERE org_id = ?", (scope.org_id,)
            ).fetchone()
        return int(row[0])

    def known_user_ids(self, *, scope: MemoryScope) -> list[str]:
        """Distinct non-null ``user_id`` values of the tenant's nodes (sorted)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT user_id FROM mv2_nodes WHERE org_id = ? "
                "AND user_id IS NOT NULL ORDER BY user_id",
                (scope.org_id,),
            ).fetchall()
        return [str(r[0]) for r in rows]

    def close(self) -> None:
        """Close the underlying connection."""
        with self._lock:
            self._conn.close()

    # ── internals ─────────────────────────────────────────────────────────────

    def _erasable(self, record_id: str, scope: MemoryScope) -> bool:
        """Deletion predicate: the node lies inside ``scope`` (never a wider/sibling one).

        Unknown nodes carry no scope; only an org-wide erasure may clear their edges.
        """
        row = self._conn.execute(
            "SELECT user_id, agent, thread_id FROM mv2_nodes WHERE org_id = ? AND id = ?",
            (scope.org_id, record_id),
        ).fetchone()
        if row is None:
            return scope.user_id is None and scope.agent is None and scope.thread_id is None
        node_scope = MemoryScope(
            org_id=scope.org_id, user_id=row[0], agent=row[1], thread_id=row[2]
        )
        return within_erasure_scope(scope, node_scope)

    def _node_visible(self, record_id: str, scope: MemoryScope) -> bool:
        """Unknown nodes are visible (edge is org-scoped); known ones must be covered."""
        row = self._conn.execute(
            "SELECT user_id, agent, thread_id FROM mv2_nodes WHERE org_id = ? AND id = ?",
            (scope.org_id, record_id),
        ).fetchone()
        if row is None:
            return True
        node_scope = MemoryScope(
            org_id=scope.org_id, user_id=row[0], agent=row[1], thread_id=row[2]
        )
        return scope.covers(node_scope)


__all__ = ["SqliteMemoryGraph"]
