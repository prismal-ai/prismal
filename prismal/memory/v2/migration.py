"""The ``mv2_*`` metadata contract and the legacy backfill (Phase MEM — SPEC-MEM-ADP-001/002).

v2 fields ride along legacy records under the reserved ``mv2_`` prefix, so the
legacy stores need no schema change and legacy recall keeps returning v2-written
content. This module owns that encoding (:func:`record_to_mv2` /
:func:`record_from_mv2`), the mapping of un-annotated legacy records
(:func:`legacy_record` — ``SEMANTIC`` / ``origin="legacy"`` / ``trust="unknown"``)
and :func:`migrate_legacy_records`, an idempotent, annotate-only backfill.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from prismal.memory.v2.types import (
    MemoryEdge,
    MemoryRecord,
    MemoryScope,
    MemoryType,
    Provenance,
    RelationKind,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from prismal.memory.v2.ports import MemoryBackendPort

MV2_PREFIX = "mv2_"
MV2_VERSION = 1
LEGACY_CONFIDENCE = 0.5


def record_to_mv2(record: MemoryRecord) -> dict[str, Any]:
    """Encode every v2 field of ``record`` (except content) as JSON-safe ``mv2_*`` keys."""
    prov = record.provenance
    return {
        "mv2_version": MV2_VERSION,
        "mv2_id": record.id,
        "mv2_type": record.type.value,
        "mv2_org": record.scope.org_id,
        "mv2_scope": {
            "user_id": record.scope.user_id,
            "agent": record.scope.agent,
            "thread_id": record.scope.thread_id,
        },
        "mv2_prov": {
            "origin": prov.origin,
            "source_message_ids": list(prov.source_message_ids),
            "session_id": prov.session_id,
            "agent": prov.agent,
            "created_at": prov.created_at,
            "tainted": prov.tainted,
            "trust": prov.trust,
            "taint_sources": list(prov.taint_sources),
        },
        "mv2_edges": [
            {
                "src_id": e.src_id,
                "dst_id": e.dst_id,
                "relation": e.relation.value,
                "confidence": e.confidence,
                "valid_from": e.valid_from,
                "valid_to": e.valid_to,
            }
            for e in record.edges
        ],
        "mv2_superseded_by": record.superseded_by,
        "mv2_confidence": record.confidence,
        "mv2_rec_version": record.version,
        "mv2_expires_at": record.expires_at,
        "mv2_legacy_ref": record.legacy_ref,
    }


def has_mv2(meta: Mapping[str, Any]) -> bool:
    """True when ``meta`` already carries the v2 annotation marker."""
    return "mv2_version" in meta


def record_from_mv2(meta: Mapping[str, Any], *, content: str) -> MemoryRecord:
    """Decode a record written by :func:`record_to_mv2`; ``content`` comes from the store."""
    scope = meta["mv2_scope"]
    prov = meta["mv2_prov"]
    return MemoryRecord(
        id=str(meta["mv2_id"]),
        type=MemoryType(meta["mv2_type"]),
        content=content,
        scope=MemoryScope(
            org_id=str(meta["mv2_org"]),
            user_id=scope.get("user_id"),
            agent=scope.get("agent"),
            thread_id=scope.get("thread_id"),
        ),
        provenance=Provenance(
            origin=prov["origin"],
            source_message_ids=tuple(prov["source_message_ids"]),
            session_id=prov["session_id"],
            agent=prov["agent"],
            created_at=float(prov["created_at"]),
            tainted=bool(prov["tainted"]),
            trust=prov["trust"],
            taint_sources=tuple(prov["taint_sources"]),
        ),
        confidence=float(meta["mv2_confidence"]),
        version=int(meta["mv2_rec_version"]),
        expires_at=meta["mv2_expires_at"],
        superseded_by=meta["mv2_superseded_by"],
        edges=tuple(
            MemoryEdge(
                src_id=e["src_id"],
                dst_id=e["dst_id"],
                relation=RelationKind(e["relation"]),
                confidence=float(e["confidence"]),
                valid_from=float(e["valid_from"]),
                valid_to=e["valid_to"],
            )
            for e in meta["mv2_edges"]
        ),
        legacy_ref=meta["mv2_legacy_ref"],
    )


def legacy_record(
    *,
    record_id: str,
    content: str,
    org_id: str,
    user_id: str | None,
    session_id: str | None,
    created_at: float,
    expires_at: float | None,
) -> MemoryRecord:
    """Map an un-annotated legacy entry: ``SEMANTIC``, ``origin="legacy"``, ``trust="unknown"``."""
    return MemoryRecord(
        id=record_id,
        type=MemoryType.SEMANTIC,
        content=content,
        scope=MemoryScope(org_id=org_id, user_id=user_id),
        provenance=Provenance(
            origin="legacy",
            source_message_ids=(),
            session_id=session_id,
            agent=None,
            created_at=created_at,
            tainted=False,
            trust="unknown",
        ),
        confidence=LEGACY_CONFIDENCE,
        expires_at=expires_at,
        legacy_ref=record_id,
    )


@dataclass(frozen=True)
class LegacyEntry:
    """One record found in a legacy storage location, and whether it carries ``mv2_*``."""

    record: MemoryRecord
    annotated: bool


@runtime_checkable
class LegacyAnnotatable(Protocol):
    """Backfill hooks of the legacy-store adapters (not part of ``MemoryBackendPort``)."""

    async def scan_legacy(
        self, *, scope: MemoryScope, limit: int, offset: int
    ) -> Sequence[LegacyEntry]: ...

    async def annotate(self, record: MemoryRecord) -> bool: ...


@dataclass(frozen=True)
class MigrationReport:
    """Outcome of one backfill run (``annotated`` = would-annotate on a dry run)."""

    scanned: int
    annotated: int
    skipped: int
    dry_run: bool


_FALLBACK_LIST_LIMIT = 1_000_000


async def migrate_legacy_records(
    backend: MemoryBackendPort,
    *,
    scope: MemoryScope,
    dry_run: bool = True,
    batch_size: int = 500,
) -> MigrationReport:
    """Annotate legacy records visible to ``scope`` with ``mv2_*`` metadata.

    Idempotent (records already carrying ``mv2_version`` are skipped), never
    deletes and never rewrites content: only ``mv2_*`` metadata is added, with
    ``origin="legacy"`` / ``trust="unknown"``. ``dry_run`` (the default) counts
    without writing. A backend without the :class:`LegacyAnnotatable` hooks holds
    only v2-native records, so everything it lists is reported as skipped.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be > 0")
    if not isinstance(backend, LegacyAnnotatable):
        listed = await backend.list(scope=scope, limit=_FALLBACK_LIST_LIMIT)
        return MigrationReport(len(listed), 0, len(listed), dry_run)
    scanned = annotated = skipped = 0
    offset = 0
    while True:
        batch = await backend.scan_legacy(scope=scope, limit=batch_size, offset=offset)
        for entry in batch:
            scanned += 1
            if entry.annotated:
                skipped += 1
            elif dry_run or await backend.annotate(entry.record):
                annotated += 1
            else:
                skipped += 1
        if len(batch) < batch_size:
            break
        offset += len(batch)
    return MigrationReport(scanned, annotated, skipped, dry_run)


__all__ = [
    "LEGACY_CONFIDENCE",
    "MV2_PREFIX",
    "MV2_VERSION",
    "LegacyAnnotatable",
    "LegacyEntry",
    "MigrationReport",
    "has_mv2",
    "legacy_record",
    "migrate_legacy_records",
    "record_from_mv2",
    "record_to_mv2",
]
