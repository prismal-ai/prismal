"""Legacy-shaped facades over ``MemoryPort`` (Phase MEM — SPEC-MEM-ADP-002).

Opt-in classes for callers that want v2 behaviour (gated writes, budgeted recall,
tenancy) behind the familiar legacy method names. The legacy classes themselves
are untouched.

Mapping:

* ``save`` / ``store_fact`` → :meth:`MemoryPort.write`;
* ``recall`` / ``recall_facts`` → :meth:`MemoryPort.recall`, results reshaped to
  the legacy return types; errors degrade to ``[]`` like legacy;
* destructive methods (``clear``, ``delete_fact``) **never delete**: they file a
  pending forget request (``ForgetPolicy.request``) and return its id instead of
  a count / ``None`` — the documented behaviour difference. Without a forget
  requester they raise ``MemoryForgetDenied`` (fail-closed);
* ``expire`` is a no-op returning ``0``: expiry is a retention rule enforced by
  ``ForgetPolicy.retention_rules()``, not a caller action;
* ``ttl_days`` is ignored: retention comes from ``memory_retention_days_by_type``;
* ``store_fact(user_id=…)`` / per-user namespaces only *narrow* the context scope:
  a context already bound to another user raises ``MemoryTenancyError``.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from prismal.core.exceptions import MemoryForgetDenied, MemoryTenancyError
from prismal.core.logging import get_logger
from prismal.memory.long_term import MemoryEntry
from prismal.memory.v2.types import (
    ForgetSelector,
    MemoryCandidate,
    MemoryContext,
    MemoryRecord,
    MemoryType,
    RecallQuery,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from prismal.memory.v2.ports import MemoryPort

logger = get_logger("prismal.memory.v2.facades")

_FAR_FUTURE = datetime(9999, 12, 31)  # noqa: DTZ001 — legacy MemoryEntry uses naive UTC
_PER_USER_NAMESPACES = frozenset({"preferences", "learned_facts", "agent_feedback"})


class _HasRequestId(Protocol):
    @property
    def request_id(self) -> str: ...


class ForgetRequester(Protocol):
    """The slice of ``ForgetPolicy`` the facades need: file a pending request."""

    def request(self, selector: ForgetSelector, *, requested_by: str) -> _HasRequestId: ...


def _naive_utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, UTC).replace(tzinfo=None)


def _resolve_budget(budget_tokens: int | None) -> int:
    if budget_tokens is not None:
        return budget_tokens
    from prismal.core.config import get_settings

    return int(get_settings().memory_recall_budget_tokens)


class _FacadeBase:
    def __init__(
        self,
        port: MemoryPort,
        *,
        ctx_factory: Callable[[], MemoryContext],
        forget: ForgetRequester | None = None,
        budget_tokens: int | None = None,
    ) -> None:
        self._port = port
        self._ctx_factory = ctx_factory
        self._forget = forget
        self._budget = budget_tokens

    def _request_forget(self, selector: ForgetSelector, ctx: MemoryContext) -> str:
        if self._forget is None:
            raise MemoryForgetDenied(
                "legacy destructive call routed to v2 without a ForgetPolicy; nothing deleted"
            )
        return self._forget.request(selector, requested_by=ctx.actor).request_id

    async def _recall(self, query: RecallQuery, ctx: MemoryContext) -> list[MemoryRecord] | None:
        try:
            result = await self._port.recall(
                query, ctx=ctx, budget_tokens=_resolve_budget(self._budget)
            )
        except Exception as exc:  # legacy recall degrades gracefully
            logger.warning("memory_v2_facade_recall_failed", error=type(exc).__name__)
            return None
        return [item.record for item in result.items]


class LegacyLongTermFacade(_FacadeBase):
    """``LongTermMemory``-shaped API over a ``MemoryPort``."""

    async def save(self, content: str, session_id: str) -> str:
        """Write ``content`` via ``MemoryPort.write``; returns the record id or ``""``."""
        ctx = dataclasses.replace(self._ctx_factory(), session_id=session_id)
        outcome = await self._port.write(
            MemoryCandidate(content=content, source_message_ids=(), origin="conversation"),
            ctx=ctx,
        )
        return outcome.record_id or ""

    async def recall(
        self,
        query: str,
        session_id: str | None = None,
        k: int = 5,
    ) -> list[MemoryEntry]:
        """Recall via ``MemoryPort.recall``, reshaped to legacy ``MemoryEntry`` objects."""
        records = await self._recall(RecallQuery(text=query), self._ctx_factory())
        entries: list[MemoryEntry] = []
        for rec in records or []:
            if session_id is not None and rec.provenance.session_id != session_id:
                continue
            entries.append(
                MemoryEntry(
                    id=rec.id,
                    session_id=rec.provenance.session_id or "",
                    content=rec.content,
                    created_at=_naive_utc(rec.provenance.created_at),
                    expires_at=(
                        _naive_utc(rec.expires_at) if rec.expires_at is not None else _FAR_FUTURE
                    ),
                )
            )
        return entries[: max(k, 0)]

    async def clear(self, session_id: str | None = None) -> str:
        """File a pending forget request for the scope (or session); returns its id."""
        ctx = self._ctx_factory()
        selector = ForgetSelector(
            scope=ctx.scope,
            session_id=session_id,
            reason=f"legacy clear(session_id={session_id!r})",
        )
        return self._request_forget(selector, ctx)

    async def expire(self) -> int:
        """No-op: expiry is enforced by ``ForgetPolicy`` retention rules."""
        return 0


class LegacyFactStoreFacade(_FacadeBase):
    """``LongTermMemoryStore``-shaped fact API over a ``MemoryPort``."""

    @property
    def backend_name(self) -> str:
        """Health-check identifier (legacy ``LongTermMemoryStore.backend_name``)."""
        return "memory_v2"

    @staticmethod
    def _as_user(ctx: MemoryContext, user_id: str) -> MemoryContext:
        """Narrow ``ctx`` to ``user_id``; never widen or switch a user-bound context.

        A context already bound to a user only accepts that same user — a caller
        cannot reach another user's (or the org-wide) scope by passing a different
        ``user_id`` / per-user namespace (fail-closed, ``MemoryTenancyError``).
        """
        bound = ctx.scope.user_id
        if bound is not None and bound != user_id:
            logger.warning("memory_facade_user_mismatch", org_id=ctx.scope.org_id)
            raise MemoryTenancyError("legacy facade user_id conflicts with the bound context user")
        return dataclasses.replace(ctx, scope=dataclasses.replace(ctx.scope, user_id=user_id))

    def _ctx_for(self, namespace: tuple[str, ...]) -> MemoryContext:
        ctx = self._ctx_factory()
        if len(namespace) >= 2 and namespace[0] in _PER_USER_NAMESPACES:
            ctx = self._as_user(ctx, namespace[1])
        return ctx

    @staticmethod
    def _type_for(namespace: tuple[str, ...]) -> MemoryType | None:
        return MemoryType.PREFERENCE if namespace[:1] == ("preferences",) else None

    async def store_fact(
        self,
        user_id: str,
        namespace: tuple[str, ...],
        key: str,
        value: Any,
        ttl_days: int | None = None,  # noqa: ARG002 — legacy signature; v2 retention applies
    ) -> None:
        """Write ``str(value)`` via ``MemoryPort.write`` (``ttl_days`` is ignored)."""
        ctx = self._as_user(self._ctx_factory(), user_id)
        await self._port.write(
            MemoryCandidate(
                content=str(value),
                source_message_ids=(),
                origin="conversation",
                suggested_type=self._type_for(namespace),
                metadata={"legacy_namespace": "/".join(namespace), "legacy_key": key},
            ),
            ctx=ctx,
        )

    async def recall_facts(
        self,
        namespace: tuple[str, ...],
        query: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Recall via ``MemoryPort.recall``, reshaped to legacy fact dictionaries."""
        if limit <= 0:
            return []
        mtype = self._type_for(namespace)
        records = await self._recall(
            RecallQuery(text=query, types=frozenset({mtype}) if mtype else None),
            self._ctx_for(namespace),
        )
        return [
            {
                "key": rec.id,
                "value": rec.content,
                "user_id": rec.scope.user_id or "",
                "created_at": rec.provenance.created_at,
            }
            for rec in (records or [])[:limit]
        ]

    async def delete_fact(self, namespace: tuple[str, ...], key: str) -> str:
        """File a pending forget request for record ``key``; returns its id."""
        ctx = self._ctx_factory()
        selector = ForgetSelector(
            record_ids=(key,),
            scope=ctx.scope,
            reason=f"legacy delete_fact({'/'.join(namespace)})",
        )
        return self._request_forget(selector, ctx)


__all__ = ["ForgetRequester", "LegacyFactStoreFacade", "LegacyLongTermFacade"]
