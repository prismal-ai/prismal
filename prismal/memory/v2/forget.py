"""Forget — rule-based or HITL-only deletion (Phase MEM — SPEC-MEM-DEL-001).

``ForgetPolicy`` is the **only** caller of ``MemoryBackendPort.purge`` in
``prismal/**`` (AST-guarded by ``tests/unit/memory/v2/test_import_guards.py``).
Deletion is accepted from exactly two actors:

- ``ForgetActor(kind="rule")`` — only for a selector this policy itself issued
  through :meth:`ForgetPolicy.retention_rules` (one-shot), so a rule actor cannot
  be paired with a hand-crafted selector;
- ``ForgetActor(kind="human", approval_id=...)`` — only when ``approval_id``
  resolves to an **approved** :class:`ForgetRequest` whose selector equals the
  executed one (one-shot; when a ``PermissionManager`` is injected its grant is
  re-checked at execution time).

Anything else (agents, decision models, LLMs, consolidation) raises
:class:`MemoryForgetDenied`, is audited and counted
(``memory_forget{actor,outcome="denied"}``). Agents only ever get
:func:`build_request_memory_forget_tool`, which creates *pending* requests.
Audit is hash-first: selector hash, counts, actor kind, approval id — never content.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal, NoReturn, Protocol

from prismal.core.exceptions import (
    MemoryErasureIncomplete,
    MemoryForgetDenied,
    MemoryTenancyError,
)
from prismal.memory.v2.types import (
    ForgetActor,
    ForgetSelector,
    MemoryRecord,
    MemoryScope,
    MemoryType,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from langchain_core.tools import BaseTool

    from prismal.agents.extension.ports import AuditPort
    from prismal.core.config import Settings
    from prismal.memory.v2.ports import MemoryBackendPort, MemoryGraphPort

RequestStatus = Literal["pending", "approved", "rejected", "executed"]

DAY_S = 86_400.0
SUPERSEDED_REASON = "retention:superseded"
_LIST_LIMIT = 1_000_000
_PERMISSION_RESOURCE = "memory_forget:{request_id}"
_APPROVAL_TTL_S = 3600


_NARROWING_FIELDS = ("user_id", "agent", "thread_id")


def within_erasure_scope(selector_scope: MemoryScope, record_scope: MemoryScope) -> bool:
    """Deletion predicate: the record lies *inside* ``selector_scope`` (same width or narrower).

    Every non-``None`` field of the selector must equal the record's; a wider
    record (``None`` where the selector is set) never matches. This is **not**
    :meth:`MemoryScope.covers`, which is the read predicate (a narrow request may
    *see* wider shared records but must never *delete* them).
    """
    if record_scope.org_id != selector_scope.org_id:
        return False
    for name in _NARROWING_FIELDS:
        want = getattr(selector_scope, name)
        if want is not None and getattr(record_scope, name) != want:
            return False
    return True


class _Permissions(Protocol):
    """The slice of :class:`prismal.security.permissions.PermissionManager` used here."""

    async def grant(
        self,
        permission_type: Any,
        resource: str,
        ttl_seconds: int | None = 3600,
        reason: str = "",
        *,
        identity: str | None = None,
    ) -> None: ...

    async def check(
        self, permission_type: Any, resource: str, *, identity: str | None = None
    ) -> bool: ...


@dataclass(frozen=True)
class ForgetRequest:
    """A deletion request; only an ``approved`` request authorizes a human execution."""

    request_id: str
    selector: ForgetSelector
    created_at: float
    requested_by: str  # agent or principal; may never approve or execute it
    status: RequestStatus
    approved_by: str | None = None  # the only principal allowed to execute it


def selector_hash(selector: ForgetSelector) -> str:
    """Stable sha256 of a selector (audit key — never the selector's content)."""
    scope = selector.scope
    canon = {
        "ids": sorted(selector.record_ids),
        "scope": None
        if scope is None
        else [scope.org_id, scope.user_id, scope.agent, scope.thread_id],
        "types": None if selector.types is None else sorted(t.value for t in selector.types),
        "older_than": selector.older_than,
        "include_derived": selector.include_derived,
        "reason": selector.reason,
        "session_id": selector.session_id,
    }
    return hashlib.sha256(json.dumps(canon, sort_keys=True).encode()).hexdigest()


def _count(actor: str, outcome: str) -> None:
    with contextlib.suppress(Exception):
        from prismal.monitoring.otel import OTelManager

        OTelManager().increment_counter(
            "memory_forget", attributes={"actor": actor, "outcome": outcome}
        )


class ForgetPolicy:
    """Gatekeeper of every memory deletion (rule / approved human only)."""

    def __init__(
        self,
        *,
        backend: MemoryBackendPort,
        graph: MemoryGraphPort | None,
        audit: AuditPort,
        permissions: _Permissions | None,
        settings: Settings | None = None,
        clock: Callable[[], float] = time.time,
        approver: Callable[[ForgetRequest, str], Awaitable[bool]] | None = None,
        derived_purgers: Sequence[Callable[[tuple[str, ...], MemoryScope], Awaitable[int]]] = (),
    ) -> None:
        if settings is None:
            from prismal.core.config import get_settings

            settings = get_settings()
        self._backend = backend
        self._graph = graph
        self._audit = audit
        self._permissions = permissions
        self._settings = settings
        self._clock = clock
        self._approver = approver
        self._derived_purgers = tuple(derived_purgers)
        self._requests: dict[str, ForgetRequest] = {}
        self._issued_rules: set[ForgetSelector] = set()
        # Purged record ids whose derived artifacts still await a successful purge,
        # per tenant scope; retried by the next execution in that scope. The flag
        # records whether derived-artifact hooks apply (erasure) or not (TTL expiry).
        self._pending_derived: dict[MemoryScope, dict[str, bool]] = {}

    # ── requests (HITL) ──────────────────────────────────────────────────────

    def request(self, selector: ForgetSelector, *, requested_by: str) -> ForgetRequest:
        """Record a *pending* request; nothing is deleted."""
        if selector.scope is None:
            raise MemoryTenancyError("ForgetSelector.scope is required")
        req = ForgetRequest(
            request_id=f"fgt_{uuid.uuid4().hex}",
            selector=selector,
            created_at=self._clock(),
            requested_by=requested_by,
            status="pending",
        )
        self._requests[req.request_id] = req
        self._log(
            "memory.forget_requested",
            {
                "request_id": req.request_id,
                "selector_hash": selector_hash(selector),
                "org_id": selector.scope.org_id,
            },
        )
        return req

    def get_request(self, request_id: str) -> ForgetRequest | None:
        return self._requests.get(request_id)

    def pending(self) -> tuple[ForgetRequest, ...]:
        return tuple(r for r in self._requests.values() if r.status == "pending")

    async def approve(self, request_id: str, *, principal: str) -> ForgetRequest:
        """Human approval (``hitl_gate`` / dashboard). The approver may still veto."""
        req = self._pending_or_deny(request_id)
        if principal == req.requested_by:
            self._deny("human", req.selector, "self_approval")
        if self._approver is not None and not await self._approver(req, principal):
            return self._set_status(req, "rejected", principal)
        if self._permissions is not None:
            from prismal.security.permissions import PermissionType

            await self._permissions.grant(
                PermissionType.DATABASE_WRITE,
                _PERMISSION_RESOURCE.format(request_id=request_id),
                ttl_seconds=_APPROVAL_TTL_S,
                reason="memory forget approval",
                identity=principal,
            )
        return self._set_status(req, "approved", principal)

    async def reject(self, request_id: str, *, principal: str) -> ForgetRequest:
        return self._set_status(self._pending_or_deny(request_id), "rejected", principal)

    def _pending_or_deny(self, request_id: str) -> ForgetRequest:
        req = self._requests.get(request_id)
        if req is None or req.status != "pending":
            raise MemoryForgetDenied(f"no pending forget request {request_id!r}")
        return req

    def _set_status(
        self, req: ForgetRequest, status: RequestStatus, principal: str
    ) -> ForgetRequest:
        new = replace(req, status=status)
        if status == "approved":
            new = replace(new, approved_by=principal)
        self._requests[req.request_id] = new
        self._log(
            "memory.forget_request_" + status,
            {"request_id": req.request_id, "principal": principal},
        )
        return new

    # ── retention rules (MEM4-05) ────────────────────────────────────────────

    def retention_rules(self, *, scope: MemoryScope | None = None) -> Sequence[ForgetSelector]:
        """Selectors for the configured TTLs; only these are executable by a rule actor.

        ``scope`` (one tenant) is required to make a selector executable — rule
        execution is per org like every other memory operation.
        """
        now = self._clock()
        rules: list[ForgetSelector] = []
        for type_name, days in sorted(self._settings.memory_retention_days_by_type.items()):
            if days <= 0:
                continue
            rules.append(
                ForgetSelector(
                    scope=scope,
                    types=frozenset({MemoryType(type_name)}),
                    older_than=now - days * DAY_S,
                    include_derived=False,  # TTL expiry is not an erasure request
                    reason=f"retention:{type_name}",
                )
            )
        sup_days = self._settings.memory_superseded_retention_days
        if sup_days > 0:
            rules.append(
                ForgetSelector(
                    scope=scope,
                    older_than=now - sup_days * DAY_S,
                    include_derived=False,
                    reason=SUPERSEDED_REASON,
                )
            )
        self._issued_rules.update(rules)
        return tuple(rules)

    # ── execution ────────────────────────────────────────────────────────────

    async def execute(self, selector: ForgetSelector, *, actor: ForgetActor) -> int:
        """Delete what ``selector`` targets; raises ``MemoryForgetDenied`` for bad actors."""
        if not isinstance(actor, ForgetActor):
            self._deny("other", selector, "not_a_forget_actor")
        scope = selector.scope
        if scope is None:
            raise MemoryTenancyError("ForgetSelector.scope is required")
        request: ForgetRequest | None = None
        if actor.kind == "rule":
            if selector not in self._issued_rules:
                self._deny("rule", selector, "selector_not_from_retention_rules")
            self._issued_rules.discard(selector)
        elif actor.kind == "human":
            request = await self._authorize_human(selector, actor)
        else:  # pragma: no cover - ForgetActor.kind is a closed Literal
            self._deny("other", selector, "unknown_actor_kind")

        targets = await self._targets(selector, scope)
        derived: set[str] = set()
        out_of_scope = 0
        if selector.include_derived and targets:
            derived, out_of_scope = await self._derived(targets, scope)
        ids = sorted(targets | derived)
        count = await self._backend.purge(ids, scope=scope, _authorized=actor) if ids else 0
        work = self._pending_derived.pop(scope, {})
        erased = set(ids)
        if selector.include_derived:
            # Ids already gone from the tenant (an earlier, interrupted execution —
            # possibly before a restart) still get their derived artifacts purged.
            erased.update(await self._gone(selector.record_ids, scope))
        for rid in erased:
            work[rid] = work.get(rid, False) or selector.include_derived
        errors = 0
        failed: dict[str, bool] = {}
        for hooks in (True, False):
            group = tuple(sorted(rid for rid, flag in work.items() if flag is hooks))
            if not group:
                continue
            group_errors = await self._purge_derived_artifacts(group, scope, actor, hooks=hooks)
            if group_errors:
                errors += group_errors
                failed.update(dict.fromkeys(group, hooks))
        outcome = "incomplete" if errors else "executed"
        if errors:
            self._pending_derived[scope] = failed
        elif request is not None:
            self._requests[request.request_id] = replace(request, status="executed")
        _count(actor.kind, outcome)
        self._log(
            "memory.forget",
            {
                "outcome": outcome,
                "actor": actor.kind,
                "approval_id": actor.approval_id,
                "selector_hash": selector_hash(selector),
                "org_id": scope.org_id,
                "count": count,
                "derived_count": len(derived),
                "derived_out_of_scope": out_of_scope,
                "derived_errors": errors,
            },
        )
        if errors:
            raise MemoryErasureIncomplete(
                f"{errors} derived purge(s) failed; the deletion is retryable"
            )
        return count

    async def _authorize_human(self, selector: ForgetSelector, actor: ForgetActor) -> ForgetRequest:
        req = self._requests.get(actor.approval_id or "")
        if req is None or req.status != "approved":
            self._deny("human", selector, "approval_not_found_or_not_approved")
        if req.selector != selector:
            self._deny("human", selector, "selector_mismatch")
        if actor.principal == req.requested_by or actor.principal != req.approved_by:
            self._deny("human", selector, "principal_not_approver")
        if self._permissions is not None:
            from prismal.security.permissions import PermissionType

            resource = _PERMISSION_RESOURCE.format(request_id=req.request_id)
            if not await self._permissions.check(
                PermissionType.DATABASE_WRITE, resource, identity=actor.principal
            ):
                self._deny("human", selector, "permission_not_granted")
        return req

    def _deny(self, actor: str, selector: ForgetSelector, reason: str) -> NoReturn:
        _count(actor, "denied")
        self._log(
            "memory.forget",
            {
                "outcome": "denied",
                "actor": actor,
                "reason": reason,
                "selector_hash": selector_hash(selector),
            },
        )
        raise MemoryForgetDenied(f"memory deletion denied ({actor}: {reason})")

    async def _targets(self, selector: ForgetSelector, scope: MemoryScope) -> set[str]:
        if selector.record_ids:
            candidates: list[MemoryRecord] = []
            for rid in selector.record_ids:
                rec = await self._backend.get(rid, scope=scope)
                if rec is not None:
                    candidates.append(rec)
        else:
            candidates = list(await self._backend.list(scope=scope, limit=_LIST_LIMIT))
        superseded_only = selector.reason == SUPERSEDED_REASON
        out: set[str] = set()
        for rec in candidates:
            if not within_erasure_scope(scope, rec.scope):
                continue
            if selector.types is not None and rec.type not in selector.types:
                continue
            if selector.session_id is not None and rec.provenance.session_id != (
                selector.session_id
            ):
                continue
            if superseded_only:
                if rec.superseded_by is None:
                    continue
                at = await self._superseded_at(rec, scope)
            else:
                at = rec.provenance.created_at
            if selector.older_than is not None and at >= selector.older_than:
                continue
            out.add(rec.id)
        return out

    async def _gone(self, record_ids: Sequence[str], scope: MemoryScope) -> set[str]:
        """Ids absent from the whole tenant (never a live record of a wider scope)."""
        tenant = MemoryScope(org_id=scope.org_id)
        return {rid for rid in record_ids if await self._backend.get(rid, scope=tenant) is None}

    async def _superseded_at(self, rec: MemoryRecord, scope: MemoryScope) -> float:
        newer = await self._backend.get(rec.superseded_by or "", scope=scope)
        return newer.provenance.created_at if newer is not None else rec.provenance.created_at

    async def _derived(self, targets: set[str], scope: MemoryScope) -> tuple[set[str], int]:
        """Records derived from a purged one (transitively), and how many lie outside ``scope``.

        A record is derived when its provenance cites a purged record or when it
        was superseded by one (an older version of erased content). Only records
        inside the selector scope are purged; wider ones are counted for the audit
        so a human can file a wider request.
        """
        records = [
            r
            for r in await self._backend.list(scope=scope, limit=_LIST_LIMIT)
            if scope.covers(r.scope)
        ]
        gone = set(targets)
        derived: set[str] = set()
        out_of_scope: set[str] = set()
        changed = True
        while changed:
            changed = False
            for rec in records:
                if rec.id in gone or rec.id in out_of_scope:
                    continue
                cites = gone.intersection(rec.provenance.source_message_ids)
                if not cites and rec.superseded_by not in gone:
                    continue
                changed = True
                if within_erasure_scope(scope, rec.scope):
                    gone.add(rec.id)
                    derived.add(rec.id)
                else:
                    out_of_scope.add(rec.id)
        # Edges held by surviving records that point at purged ones.
        for rec in records:
            if rec.id in gone or not rec.edges:
                continue
            kept = tuple(e for e in rec.edges if e.src_id not in gone and e.dst_id not in gone)
            if kept != rec.edges:
                await self._backend.update(replace(rec, edges=kept))
        return derived, len(out_of_scope)

    async def _purge_derived_artifacts(
        self, ids: tuple[str, ...], scope: MemoryScope, actor: ForgetActor, *, hooks: bool
    ) -> int:
        """Purge graph nodes (always) and derived-artifact hooks (erasure only); count failures."""
        errors = 0
        if self._graph is not None:
            try:
                await self._purge_graph(ids, scope, actor)
            except Exception:
                errors += 1
        for hook in self._derived_purgers if hooks else ():
            try:
                await hook(ids, scope)
            except Exception:
                errors += 1
        return errors

    async def _purge_graph(
        self, ids: tuple[str, ...], scope: MemoryScope, actor: ForgetActor
    ) -> None:
        graph = self._graph
        purge_nodes = getattr(graph, "purge_nodes", None)
        if callable(purge_nodes):
            await purge_nodes(ids, scope=scope, _authorized=actor)
            return
        assert graph is not None
        now = self._clock()
        for rid in ids:
            for edge in await graph.neighbors(rid, scope=scope, as_of=None):
                await graph.invalidate_edge(edge.src_id, edge.dst_id, at=now, scope=scope)

    def _log(self, event: str, payload: dict[str, Any]) -> None:
        with contextlib.suppress(Exception):
            self._audit.log_event(event, payload)


def build_request_memory_forget_tool(
    policy: ForgetPolicy, *, scope: MemoryScope, requested_by: str
) -> BaseTool:
    """The only agent-facing forget tool: it creates a *pending* request, never deletes.

    ``scope`` is bound here (by the host), so an agent cannot target another tenant.
    """
    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel, Field

    class _Args(BaseModel):
        record_ids: list[str] = Field(description="Ids of the memory records to forget.")
        reason: str = Field(default="", description="Why these memories should be forgotten.")

    def _run(record_ids: list[str], reason: str = "") -> str:
        req = policy.request(
            ForgetSelector(record_ids=tuple(record_ids), scope=scope, reason=reason),
            requested_by=requested_by,
        )
        return (
            f"Forget request {req.request_id} created with status pending; "
            "a human must approve it before anything is deleted."
        )

    async def _arun(record_ids: list[str], reason: str = "") -> str:
        return _run(record_ids, reason)

    return StructuredTool.from_function(
        func=_run,
        coroutine=_arun,
        name="request_memory_forget",
        description=(
            "Ask a human to approve forgetting specific memory records. "
            "Creates a pending request; it does not delete anything."
        ),
        args_schema=_Args,
    )


__all__ = [
    "SUPERSEDED_REASON",
    "ForgetPolicy",
    "ForgetRequest",
    "build_request_memory_forget_tool",
    "selector_hash",
]
