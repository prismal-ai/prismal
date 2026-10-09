"""Agentic memory value objects (Phase MEM — SPEC-MEM-TYP-001/002).

Frozen dataclasses shared by every ``memory/v2`` module. ``MemoryScope`` is the
tenancy key and rejects an empty ``org_id`` at construction (fail-closed,
SPEC-MEM-TEN-001).
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from prismal.core.exceptions import MemoryTenancyError
from prismal.decisions.types import RiskTier

if TYPE_CHECKING:
    from collections.abc import Mapping


class MemoryType(StrEnum):
    """What kind of memory a record holds."""

    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    PREFERENCE = "preference"


class RelationKind(StrEnum):
    """Relation between a new record and an existing neighbor."""

    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    UPDATES = "updates"
    UNRELATED = "unrelated"


Origin = Literal[
    "conversation", "tool", "rag", "a2a", "consolidation", "playbook", "legacy", "human"
]
Trust = Literal["trusted", "untrusted", "unknown"]
WriteStatus = Literal["stored", "merged", "skipped", "quarantined", "shadow"]
StopReason = Literal["sufficient", "budget_exhausted", "max_hops", "no_candidates", "abstained"]

_ID_LOCK = threading.Lock()
_LAST_ID: list[int] = [0]


def new_record_id() -> str:
    """A unique, lexicographically time-sortable record id (``mem_<ns hex><rand>``)."""
    with _ID_LOCK:
        now = max(time.time_ns(), _LAST_ID[0] + 1)
        _LAST_ID[0] = now
    return f"mem_{now:016x}{os.urandom(5).hex()}"


@dataclass(frozen=True)
class MemoryScope:
    """Tenancy key. ``org_id`` is mandatory; the other fields narrow the scope."""

    org_id: str
    user_id: str | None = None
    agent: str | None = None
    thread_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.org_id, str) or not self.org_id.strip():
            raise MemoryTenancyError("MemoryScope.org_id must be a non-empty string")

    def covers(self, record_scope: MemoryScope) -> bool:
        """True when a record stored under ``record_scope`` is visible to this request.

        Same ``org_id`` always required; each narrower field set on the request
        must match the record's value or the record must be wider (``None``).
        """
        if record_scope.org_id != self.org_id:
            return False
        for name in ("user_id", "agent", "thread_id"):
            want = getattr(self, name)
            have = getattr(record_scope, name)
            if want is not None and have is not None and have != want:
                return False
        return True


@dataclass(frozen=True)
class Provenance:
    """Where a record came from and whether it can be trusted."""

    origin: Origin
    source_message_ids: tuple[str, ...]
    session_id: str | None
    agent: str | None
    created_at: float
    tainted: bool
    trust: Trust
    taint_sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class MemoryContext:
    """Who is writing/recalling, for which tenant."""

    scope: MemoryScope
    session_id: str
    actor: str
    risk_tier: RiskTier = RiskTier.LOW


@dataclass(frozen=True)
class MemoryCandidate:
    """Something an agent proposes to remember."""

    content: str
    source_message_ids: tuple[str, ...]
    origin: Origin = "conversation"
    suggested_type: MemoryType | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryEdge:
    """A typed relation; invalidated via ``valid_to``, never deleted."""

    src_id: str
    dst_id: str
    relation: RelationKind
    confidence: float
    valid_from: float
    valid_to: float | None = None

    def __post_init__(self) -> None:
        if self.relation is RelationKind.UNRELATED:
            raise ValueError("RelationKind.UNRELATED edges are never materialized")


@dataclass(frozen=True)
class MemoryRecord:
    """A stored memory."""

    id: str
    type: MemoryType
    content: str
    scope: MemoryScope
    provenance: Provenance
    confidence: float
    version: int = 1
    expires_at: float | None = None
    superseded_by: str | None = None
    edges: tuple[MemoryEdge, ...] = ()
    legacy_ref: str | None = None


@dataclass(frozen=True)
class WriteOutcome:
    """Result of ``MemoryPort.write``."""

    status: WriteStatus
    record_id: str | None
    type: MemoryType | None
    edges: tuple[MemoryEdge, ...]
    backend: str
    reason: str


@dataclass(frozen=True)
class RecallQuery:
    """What to recall; ``as_of`` selects a temporal view."""

    text: str
    types: frozenset[MemoryType] | None = None
    as_of: float | None = None


@dataclass(frozen=True)
class RecalledItem:
    """One recalled record with its score and token cost."""

    record: MemoryRecord
    score: float
    tokens: int


@dataclass(frozen=True)
class RecallResult:
    """Result of ``MemoryPort.recall``; ``tokens`` never exceeds the budget."""

    items: tuple[RecalledItem, ...]
    rendered: str
    tokens: int
    stop_reason: StopReason
    views: tuple[str, ...]
    hops: int


@dataclass(frozen=True)
class ConsolidationReport:
    """Counters of one consolidation run (all zero on an idempotent re-run)."""

    scope: MemoryScope
    merged: int
    superseded: int
    summarized: int
    playbook_deltas: int
    quarantined: int
    used_batch: bool
    errors: tuple[str, ...]


@dataclass(frozen=True)
class ForgetSelector:
    """Which records a forget operation targets."""

    record_ids: tuple[str, ...] = ()
    scope: MemoryScope | None = None
    types: frozenset[MemoryType] | None = None
    older_than: float | None = None
    include_derived: bool = True
    reason: str = ""
    # Matches ``provenance.session_id`` (where conversation writes record the session).
    session_id: str | None = None


@dataclass(frozen=True)
class ForgetActor:
    """The only kinds of actor allowed to delete: a retention rule or an approved human."""

    kind: Literal["rule", "human"]
    principal: str
    approval_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind == "human" and not self.approval_id:
            raise ValueError("ForgetActor(kind='human') requires an approval_id")


__all__ = [
    "ConsolidationReport",
    "ForgetActor",
    "ForgetSelector",
    "MemoryCandidate",
    "MemoryContext",
    "MemoryEdge",
    "MemoryRecord",
    "MemoryScope",
    "MemoryType",
    "Origin",
    "Provenance",
    "RecallQuery",
    "RecallResult",
    "RecalledItem",
    "RelationKind",
    "StopReason",
    "Trust",
    "WriteOutcome",
    "WriteStatus",
    "new_record_id",
]
