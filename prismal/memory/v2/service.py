"""``MemoryService`` — the per-tenant ``MemoryPort`` (Phase MEM, MEM1-08).

Composes the write gate, recall controller, consolidator and forget policy over
one backend (and optional temporal graph) for exactly one ``org_id``
(SPEC-MEM-TEN-001, DD-MEM-005). Every call whose scope names another tenant is
rejected fail-closed with :class:`MemoryTenancyError`, counted and audited
hash-first. ``build_memory_service`` selects the backend/graph from settings;
``build_runtime(org_id)`` exposes the result as ``RuntimeContext.memory``.
"""

from __future__ import annotations

import contextlib
import hashlib
import inspect
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prismal.core.exceptions import MemoryConfigError, MemoryTenancyError
from prismal.memory.v2.consolidation import Consolidator
from prismal.memory.v2.forget import ForgetPolicy, ForgetRequest
from prismal.memory.v2.playbooks import PlaybookStore
from prismal.memory.v2.recall import RecallController, RecallPolicy
from prismal.memory.v2.taint import candidate_taint
from prismal.memory.v2.write_gate import WriteGate, WriteGatePolicy

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from prismal.agents.extension.ports import AuditPort, VectorStorePort
    from prismal.core.config import Settings
    from prismal.decisions.ports import DecisionPort
    from prismal.memory.v2.ports import MemoryBackendPort, MemoryGraphPort
    from prismal.memory.v2.types import (
        ConsolidationReport,
        ForgetActor,
        ForgetSelector,
        MemoryCandidate,
        MemoryContext,
        MemoryRecord,
        MemoryScope,
        RecallQuery,
        RecallResult,
        WriteOutcome,
    )
    from prismal.memory.v2.write_gate import LlmClassifier
    from prismal.routing.batch import BatchExecutor

MEMORY_COLLECTION_BASE = "memory_v2"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


class _NullAudit:
    """``AuditPort`` that drops everything (the host injects the real audit log)."""

    def log_event(self, event_type: str, payload: dict[str, Any]) -> None:
        del event_type, payload

    def log_node(
        self,
        *,
        node_name: str,
        session_id: str,
        status: str,
        state_hash: str,
        duration_ms: float,
    ) -> None:
        del node_name, session_id, status, state_hash, duration_ms

    def log_media(
        self,
        event: str,
        sha256: str,
        modality: str,
        size_bytes: int,
        duration_s: float | None,
    ) -> None:
        del event, sha256, modality, size_bytes, duration_s


@dataclass(frozen=True)
class MemoryHooks:
    """Host-supplied callables the memory layer never builds itself (no provider imports).

    ``llm_classifier`` serves ``memory_write_gate="llm"`` and the ``llm``
    low-confidence policy; ``summarizer`` / ``batch`` the episodic → semantic
    consolidation step; ``curator`` the playbook curation step of consolidation.
    """

    llm_classifier: LlmClassifier | None = None
    summarizer: Callable[[Sequence[MemoryRecord]], Awaitable[str]] | None = None
    batch: BatchExecutor | None = None
    curator: Callable[[MemoryScope], Awaitable[int]] | None = None


class MemoryService:
    """Agent-facing memory for one tenant; conforms to ``MemoryPort``."""

    def __init__(
        self,
        *,
        org_id: str,
        backend: MemoryBackendPort,
        decisions: DecisionPort | None = None,
        graph: MemoryGraphPort | None = None,
        audit: AuditPort | None = None,
        settings: Settings | None = None,
        playbooks: PlaybookStore | None = None,
        trusted_sources: frozenset[str] = frozenset(),
        summarizer: Callable[[Sequence[MemoryRecord]], Awaitable[str]] | None = None,
        hooks: MemoryHooks | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        hooks = hooks or MemoryHooks()
        if not org_id:
            raise MemoryTenancyError("MemoryService requires a non-empty org_id")
        if settings is None:
            from prismal.core.config import get_settings

            settings = get_settings()
        self.org_id = org_id
        self.backend = backend
        self.decisions = decisions
        self.graph = graph
        self.playbooks = playbooks
        self._settings = settings
        self._audit: AuditPort = audit if audit is not None else _NullAudit()
        self._trusted_sources = trusted_sources
        self._closed = False
        self._gate = WriteGate(
            decisions=decisions,
            backend=backend,
            policy=WriteGatePolicy.from_settings(settings),
            settings=settings,
            llm_classifier=hooks.llm_classifier,
            graph=graph,
            audit=self._audit,
            clock=clock,
        )
        self._recall = RecallController(
            decisions=decisions,
            backend=backend,
            graph=graph,
            policy=RecallPolicy.from_settings(settings),
            settings=settings,
            clock=clock,
        )
        purgers = []
        if playbooks is not None:
            store = playbooks

            async def _purge_bullets(ids: tuple[str, ...], scope: MemoryScope) -> int:
                return store.purge_citing(ids, org_id=scope.org_id)

            purgers.append(_purge_bullets)
        self.forget_policy = ForgetPolicy(
            backend=backend,
            graph=graph,
            audit=self._audit,
            permissions=None,
            settings=settings,
            clock=clock,
            derived_purgers=purgers,
        )
        self._consolidator = Consolidator(
            backend=backend,
            decisions=decisions,
            summarizer=summarizer or hooks.summarizer,
            batch=hooks.batch,
            curator=hooks.curator,
            settings=settings,
            forget=self.forget_policy,
            clock=clock,
        )

    @property
    def consolidator(self) -> Consolidator:
        """The tenant's consolidator (``register_consolidation_job(consolidator_for=…)``)."""
        return self._consolidator

    async def aclose(self) -> None:
        """Release the graph connection (idempotent); the backend is owned by its store."""
        if self._closed:
            return
        self._closed = True
        close = getattr(self.graph, "close", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result

    # ── tenancy ──────────────────────────────────────────────────────────────

    def _check(self, scope: MemoryScope | None, op: str) -> None:
        if scope is not None and scope.org_id == self.org_id:
            return
        with contextlib.suppress(Exception):
            from prismal.monitoring.otel import OTelManager

            OTelManager().increment_counter("memory_tenancy_violations", 1, {"op": op})
        with contextlib.suppress(Exception):
            self._audit.log_event(
                "memory.tenancy_violation",
                {
                    "op": op,
                    "org_hash": _hash(self.org_id),
                    "requested_org_hash": _hash(scope.org_id if scope is not None else ""),
                },
            )
        raise MemoryTenancyError(f"memory {op} outside the service tenant")

    # ── MemoryPort ───────────────────────────────────────────────────────────

    async def write(
        self,
        candidate: MemoryCandidate,
        *,
        ctx: MemoryContext,
        state: Mapping[str, Any] | None = None,
    ) -> WriteOutcome:
        """Gate and persist ``candidate``; never raises on decision failure."""
        self._check(ctx.scope, "write")
        tainted, sources = candidate_taint(
            candidate, state=state, trusted_sources=self._trusted_sources
        )
        outcome, _ = await self._gate.evaluate(
            candidate, ctx=ctx, tainted=tainted, taint_sources=sources
        )
        return outcome

    async def recall(
        self, query: RecallQuery, *, ctx: MemoryContext, budget_tokens: int
    ) -> RecallResult:
        """Budgeted recall within this tenant; never raises on decision failure."""
        self._check(ctx.scope, "recall")
        return await self._recall.run(query, ctx=ctx, budget_tokens=budget_tokens)

    async def consolidate(self, scope: MemoryScope) -> ConsolidationReport:
        """Sleep-time consolidation of exactly this tenant (supersedes, never deletes)."""
        self._check(scope, "consolidate")
        return await self._consolidator.run(scope)

    async def forget(self, selector: ForgetSelector, *, actor: ForgetActor) -> int:
        """Delete via :class:`ForgetPolicy` only (rule / approved human)."""
        self._check(selector.scope, "forget")
        return await self.forget_policy.execute(selector, actor=actor)

    def request_forget(self, selector: ForgetSelector, *, requested_by: str) -> ForgetRequest:
        """Create a pending (HITL) forget request — the only agent-facing deletion path."""
        self._check(selector.scope, "forget_request")
        return self.forget_policy.request(selector, requested_by=requested_by)


def _data_dir(settings: Settings) -> Path:
    if settings.memory_v2_data_dir:
        return Path(settings.memory_v2_data_dir)
    from prismal.memory.long_term import _DB_DEFAULT

    return _DB_DEFAULT.parent / "memory_v2"


def _tenant_file(settings: Settings, org_id: str, kind: str) -> Path:
    """Per-tenant SQLite file; the org id is hashed (no path characters, no PII)."""
    directory = _data_dir(settings)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{kind}_{_hash(org_id)}.db"


def _build_backend(
    settings: Settings, *, org_id: str, vector_store: VectorStorePort | None
) -> MemoryBackendPort:
    kind = settings.memory_v2_backend
    if kind == "memory":
        from prismal.memory.v2.adapters.in_memory import InMemoryBackend

        return InMemoryBackend()
    if kind == "langgraph_store":
        from prismal.memory.long_term_store import LongTermMemoryStore
        from prismal.memory.v2.adapters.langgraph_store import LangGraphStoreBackend

        return LangGraphStoreBackend(LongTermMemoryStore(), settings=settings)
    from prismal.composition.config_sources import collection_for
    from prismal.memory.long_term import LongTermMemory
    from prismal.memory.v2.adapters.long_term import LongTermMemoryBackend

    if vector_store is None:
        from prismal.rag.vector_store_factory import VectorStoreFactory

        vector_store = VectorStoreFactory.create(
            settings, collection_for(MEMORY_COLLECTION_BASE, org_id)
        )
    # A dedicated per-tenant file, never the shared legacy memory.db: the legacy
    # expire()/clear() would otherwise delete v2 rows outside ForgetPolicy.
    ltm = LongTermMemory(
        db_path=_tenant_file(settings, org_id, "memory"), vector_store=vector_store
    )
    return LongTermMemoryBackend(ltm, settings=settings)


def _build_graph(
    settings: Settings, *, org_id: str, graph_path: Path | str | None
) -> MemoryGraphPort | None:
    kind = settings.memory_graph_backend
    if kind == "none":
        return None
    if kind == "sqlite":
        from prismal.memory.v2.graph.sqlite import SqliteMemoryGraph

        path = graph_path if graph_path is not None else _tenant_file(settings, org_id, "graph")
        return SqliteMemoryGraph(path)
    raise MemoryConfigError(
        "PRISMAL_MEMORY_GRAPH_BACKEND=graphiti needs an injected GraphitiMemoryGraph "
        "(build_memory_service(..., graph=GraphitiMemoryGraph(client)))."
    )


def build_memory_service(
    settings: Settings,
    *,
    org_id: str | None,
    backend: MemoryBackendPort | None = None,
    graph: MemoryGraphPort | None = None,
    decisions: DecisionPort | None = None,
    audit: AuditPort | None = None,
    vector_store: VectorStorePort | None = None,
    graph_path: Path | str | None = None,
    trusted_sources: Sequence[str] = (),
    summarizer: Callable[[Sequence[MemoryRecord]], Awaitable[str]] | None = None,
    hooks: MemoryHooks | None = None,
) -> MemoryService:
    """Compose one tenant's ``MemoryService`` from settings (SPEC-MEM-TEN-001).

    Injected ``backend`` / ``graph`` win over the settings-selected adapters; the
    Graphiti graph is never built implicitly (it needs a configured client).
    """
    if not org_id:
        raise MemoryTenancyError("build_memory_service requires an org_id")
    hooks = hooks or MemoryHooks()
    if hooks.llm_classifier is None and "llm" in (
        settings.memory_write_gate,
        settings.memory_write_low_confidence,
    ):
        raise MemoryConfigError(
            "memory_write_gate / memory_write_low_confidence = 'llm' needs an "
            "llm_classifier (MemoryHooks(llm_classifier=...)); without one every write "
            "would be skipped."
        )
    if backend is None:
        backend = _build_backend(settings, org_id=org_id, vector_store=vector_store)
    if graph is None:
        graph = _build_graph(settings, org_id=org_id, graph_path=graph_path)
    playbooks = PlaybookStore(settings=settings) if settings.memory_playbooks_enabled else None
    return MemoryService(
        org_id=org_id,
        backend=backend,
        decisions=decisions,
        graph=graph,
        audit=audit,
        settings=settings,
        playbooks=playbooks,
        trusted_sources=frozenset(trusted_sources),
        summarizer=summarizer,
        hooks=hooks,
    )


__all__ = ["MEMORY_COLLECTION_BASE", "MemoryHooks", "MemoryService", "build_memory_service"]
