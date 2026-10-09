"""Agentic memory ports (Phase MEM — SPEC-MEM-PRT-001/002).

``MemoryBackendPort.purge`` is reachable only from ``ForgetPolicy``
(SPEC-MEM-DEL-001); an AST test forbids any other caller in ``prismal/**``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence

    from prismal.memory.v2.types import (
        ConsolidationReport,
        ForgetActor,
        ForgetSelector,
        MemoryCandidate,
        MemoryContext,
        MemoryEdge,
        MemoryRecord,
        MemoryScope,
        MemoryType,
        RecallQuery,
        RecallResult,
        RelationKind,
        WriteOutcome,
    )


@runtime_checkable
class MemoryPort(Protocol):
    """The agent-facing memory API. ``write``/``recall`` never raise on decision failures."""

    async def write(self, candidate: MemoryCandidate, *, ctx: MemoryContext) -> WriteOutcome: ...

    async def recall(
        self, query: RecallQuery, *, ctx: MemoryContext, budget_tokens: int
    ) -> RecallResult: ...

    async def consolidate(self, scope: MemoryScope) -> ConsolidationReport: ...

    async def forget(self, selector: ForgetSelector, *, actor: ForgetActor) -> int: ...


@runtime_checkable
class MemoryBackendPort(Protocol):
    """Storage. Every read filters by ``scope`` (``MemoryScope.covers``)."""

    name: str

    async def put(self, record: MemoryRecord) -> str: ...

    async def get(self, record_id: str, *, scope: MemoryScope) -> MemoryRecord | None: ...

    async def search(
        self,
        text: str,
        *,
        scope: MemoryScope,
        types: frozenset[MemoryType] | None,
        k: int,
        include_superseded: bool = False,
    ) -> Sequence[tuple[MemoryRecord, float]]: ...

    async def update(self, record: MemoryRecord) -> None: ...

    async def list(
        self, *, scope: MemoryScope, since: float | None = None, limit: int = 1000
    ) -> Sequence[MemoryRecord]: ...

    async def purge(
        self, record_ids: Sequence[str], *, scope: MemoryScope, _authorized: ForgetActor
    ) -> int: ...


@runtime_checkable
class MemoryGraphPort(Protocol):
    """Optional temporal graph: edges are invalidated (``valid_to``), never deleted."""

    async def upsert_node(self, record: MemoryRecord) -> None: ...

    async def add_edge(self, edge: MemoryEdge, *, scope: MemoryScope) -> None: ...

    async def invalidate_edge(
        self, src_id: str, dst_id: str, *, at: float, scope: MemoryScope
    ) -> None: ...

    async def neighbors(
        self,
        record_id: str,
        *,
        scope: MemoryScope,
        as_of: float | None,
        relations: frozenset[RelationKind] | None = None,
    ) -> Sequence[MemoryEdge]: ...


__all__ = ["MemoryBackendPort", "MemoryGraphPort", "MemoryPort"]
