"""Graphiti temporal memory graph (Phase MEM — SPEC-MEM-GRF-001, MEM6-02).

Optional ``[graph-memory]`` extra. ``graphiti_core`` is never imported at module
load: :func:`require_graphiti` probes it lazily and raises
:class:`MemoryBackendUnavailable` when the extra is absent (call it at startup
when ``memory_graph_backend == "graphiti"``).

Verified facts (MEM0-03, graphiti-core 0.30.2): ``Graphiti(...)`` always builds
an LLM client, an embedder and a reranker (OpenAI defaults) and needs a graph
driver (Neo4j by default; Kuzu is deprecated and warns, which fails
``filterwarnings=error``). This adapter therefore **requires an injected,
fully-configured client** and only calls ``add_episode``, ``search`` and
(optionally) ``remove_episode`` on it.

Design:

- Records become Graphiti *episodes* in a tenant group
  ``prismal-<sha256(org_id[, user_id])[:24]>`` (hashed: collision-safe and valid
  for Graphiti's group-id charset). Tainted records are **never** sent, because
  Graphiti runs its own LLM extraction prompt over the episode body, outside
  ``SecurePromptBuilder`` (SPEC-MEM-TNT-001).
- Explicit typed :class:`MemoryEdge` relations (``valid_from``/``valid_to``,
  ``as_of``) live in a local :class:`SqliteMemoryGraph`: Graphiti's entity edges
  are LLM-extracted and do not share our record ids.
- :meth:`GraphitiMemoryGraph.search_facts` exposes Graphiti's temporal facts;
  ``as_of`` is applied client-side over ``valid_at``/``invalid_at``/``expired_at``
  (Graphiti has no ``as_of`` parameter). Facts are derived content: render them
  as untrusted data through ``SecurePromptBuilder``.
- The record → episode index is persisted next to the local edges (same SQLite
  file) so erasure still finds a record's episode after a restart.
- Client failures on write/search are fail-open (logged; explicit edges keep
  working). Erasure is fail-**closed**: a known episode that cannot be removed
  raises :class:`MemoryErasureIncomplete` and stays indexed, so ``ForgetPolicy``
  keeps the request retryable.
"""

from __future__ import annotations

import hashlib
import importlib
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from prismal.core.exceptions import MemoryBackendUnavailable, MemoryErasureIncomplete
from prismal.core.logging import get_logger
from prismal.memory.v2.graph.sqlite import SqliteMemoryGraph

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from prismal.memory.v2.types import (
        ForgetActor,
        MemoryEdge,
        MemoryRecord,
        MemoryScope,
        RelationKind,
    )

logger = get_logger(__name__)

_EPISODES_SCHEMA = """
CREATE TABLE IF NOT EXISTS mv2_graphiti_episodes (
    org_id       TEXT NOT NULL,
    record_id    TEXT NOT NULL,
    episode_uuid TEXT NOT NULL,
    digest       TEXT NOT NULL,
    PRIMARY KEY (org_id, record_id)
);
"""

_EXTRA_HINT = "install the optional extra: pip install 'prismal[graph-memory]'"


def require_graphiti() -> None:
    """Raise :class:`MemoryBackendUnavailable` when ``graphiti_core`` is not importable."""
    try:
        importlib.import_module("graphiti_core")
    except ImportError as exc:
        raise MemoryBackendUnavailable(f"graphiti_core is not installed; {_EXTRA_HINT}") from exc


@dataclass(frozen=True)
class GraphFact:
    """One temporal fact returned by Graphiti (epoch seconds; derived, untrusted)."""

    uuid: str
    fact: str
    valid_at: float | None
    invalid_at: float | None


def _epoch(value: object) -> float | None:
    return value.timestamp() if isinstance(value, datetime) else None


def _group_id(org_id: str, user_id: str | None = None) -> str:
    key = org_id if user_id is None else f"{org_id}\x00{user_id}"
    return "prismal-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


class GraphitiMemoryGraph:
    """``MemoryGraphPort`` backed by an injected Graphiti client plus local temporal edges."""

    name = "graphiti"

    def __init__(
        self,
        client: Any | None = None,
        *,
        edges_path: str | Path = ":memory:",
        max_results: int = 10,
    ) -> None:
        """Wrap an injected ``graphiti_core.Graphiti`` client.

        Raises:
            MemoryBackendUnavailable: the extra is absent, or no client was injected.
        """
        if client is None:
            require_graphiti()
            raise MemoryBackendUnavailable(
                "GraphitiMemoryGraph requires an injected, configured Graphiti client "
                "(graph driver + LLM/embedder/reranker; see docs/memory.md)"
            )
        self._client = client
        self._edges = SqliteMemoryGraph(edges_path)
        self._max_results = max_results
        # (org_id, record_id) -> (episode uuid, content sha256), persisted
        self._lock = threading.Lock()
        self._index = sqlite3.connect(str(edges_path), check_same_thread=False)
        with self._lock:
            self._index.executescript(_EPISODES_SCHEMA)
            self._index.commit()

    # ── MemoryGraphPort ───────────────────────────────────────────────────────

    async def upsert_node(self, record: MemoryRecord) -> None:
        """Track the node locally and, unless tainted, add it as a Graphiti episode."""
        await self._edges.upsert_node(record)
        if record.provenance.tainted:
            return
        scope = record.scope
        digest = hashlib.sha256(record.content.encode("utf-8")).hexdigest()
        key = (scope.org_id, record.id)
        known = self._episode(key)
        if known is not None and known[1] == digest:
            return
        try:
            result = await self._client.add_episode(
                name=record.id,
                episode_body=record.content,
                source_description=f"prismal memory ({record.type})",
                reference_time=datetime.fromtimestamp(record.provenance.created_at, tz=UTC),
                group_id=_group_id(scope.org_id, scope.user_id),
            )
        except Exception as exc:
            logger.warning("memory_graph.graphiti_add_failed", error=type(exc).__name__)
            return
        episode_uuid = getattr(getattr(result, "episode", None), "uuid", None)
        if isinstance(episode_uuid, str):
            with self._lock:
                self._index.execute(
                    "INSERT OR REPLACE INTO mv2_graphiti_episodes VALUES (?, ?, ?, ?)",
                    (key[0], key[1], episode_uuid, digest),
                )
                self._index.commit()

    def _episode(self, key: tuple[str, str]) -> tuple[str, str] | None:
        with self._lock:
            row = self._index.execute(
                "SELECT episode_uuid, digest FROM mv2_graphiti_episodes"
                " WHERE org_id = ? AND record_id = ?",
                key,
            ).fetchone()
        return None if row is None else (str(row[0]), str(row[1]))

    async def add_edge(self, edge: MemoryEdge, *, scope: MemoryScope) -> None:
        """Store an explicit typed temporal edge (local)."""
        await self._edges.add_edge(edge, scope=scope)

    async def invalidate_edge(
        self, src_id: str, dst_id: str, *, at: float, scope: MemoryScope
    ) -> None:
        """Close open ``src → dst`` edges at *at* (never deletes)."""
        await self._edges.invalidate_edge(src_id, dst_id, at=at, scope=scope)

    async def neighbors(
        self,
        record_id: str,
        *,
        scope: MemoryScope,
        as_of: float | None,
        relations: frozenset[RelationKind] | None = None,
    ) -> Sequence[MemoryEdge]:
        """Explicit edges touching *record_id*, valid now or at ``as_of``."""
        return await self._edges.neighbors(record_id, scope=scope, as_of=as_of, relations=relations)

    # ── Graphiti facts ────────────────────────────────────────────────────────

    async def search_facts(
        self, query: str, *, scope: MemoryScope, as_of: float | None = None, k: int | None = None
    ) -> list[GraphFact]:
        """Graphiti hybrid search restricted to the tenant's groups, filtered by ``as_of``."""
        if scope.user_id is not None:
            groups = [_group_id(scope.org_id), _group_id(scope.org_id, scope.user_id)]
        else:
            groups = [_group_id(scope.org_id)] + [
                _group_id(scope.org_id, uid) for uid in self._edges.known_user_ids(scope=scope)
            ]
        try:
            edges = await self._client.search(
                query, group_ids=groups, num_results=k or self._max_results
            )
        except Exception as exc:
            logger.warning("memory_graph.graphiti_search_failed", error=type(exc).__name__)
            return []
        out: list[GraphFact] = []
        for edge in edges:
            if getattr(edge, "group_id", None) not in groups:
                continue  # defence in depth: never surface another tenant's fact
            valid_at = _epoch(getattr(edge, "valid_at", None))
            invalid_at = _epoch(getattr(edge, "invalid_at", None))
            expired_at = _epoch(getattr(edge, "expired_at", None))
            if as_of is None:
                if invalid_at is not None or expired_at is not None:
                    continue
            elif (
                (valid_at is not None and valid_at > as_of)
                or (invalid_at is not None and invalid_at <= as_of)
                or (expired_at is not None and expired_at <= as_of)
            ):
                continue
            out.append(
                GraphFact(
                    uuid=str(getattr(edge, "uuid", "")),
                    fact=str(getattr(edge, "fact", "")),
                    valid_at=valid_at,
                    invalid_at=invalid_at,
                )
            )
        return out

    # ── erasure hook (ForgetPolicy only) ─────────────────────────────────────

    async def purge_nodes(
        self, record_ids: Sequence[str], *, scope: MemoryScope, _authorized: ForgetActor
    ) -> int:
        """Erase nodes, incident edges and their Graphiti episodes (``include_derived``).

        Raises:
            MemoryErasureIncomplete: a known episode could not be removed; it stays
                indexed so a retry removes it.
        """
        removed = await self._edges.purge_nodes(record_ids, scope=scope, _authorized=_authorized)
        remover = getattr(self._client, "remove_episode", None)
        failed = 0
        for rid in record_ids:
            key = (scope.org_id, rid)
            known = self._episode(key)
            if known is None:
                continue
            if not callable(remover):
                failed += 1
                continue
            try:
                await remover(known[0])
            except Exception as exc:
                logger.warning("memory_graph.graphiti_remove_failed", error=type(exc).__name__)
                failed += 1
                continue
            with self._lock:
                self._index.execute(
                    "DELETE FROM mv2_graphiti_episodes WHERE org_id = ? AND record_id = ?", key
                )
                self._index.commit()
        if failed:
            raise MemoryErasureIncomplete(f"{failed} Graphiti episode(s) could not be removed")
        return removed

    def close(self) -> None:
        """Close the local stores (the injected client is owned by the caller)."""
        self._edges.close()
        with self._lock:
            self._index.close()


__all__ = ["GraphFact", "GraphitiMemoryGraph", "require_graphiti"]
