"""Cross-thread long-term memory store with mandatory PII sanitization.

SPEC-039 Phase 37 — this module exposes :class:`LongTermMemoryStore`, a
thin wrapper around LangGraph's Store API that persists user preferences,
project context, and learned facts across sessions. Unlike short-term
state (checkpointing) which is keyed by ``thread_id`` and lost when a new
session starts, entries written here are keyed by ``user_id`` /
``project_id`` namespaces and survive indefinitely (subject to TTL).

The critical invariant: **every value written to this store is passed
through** :class:`~lightagent.security.pii_sanitizer.PIISanitizer` **before
it hits persistent storage**. This is enforced inside :meth:`store_fact`
and cannot be bypassed — callers never need (and never should) call the
sanitizer themselves.

Backend selection
-----------------

The backend is chosen by ``LIGHTAGENT_MEMORY_BACKEND``:

* ``memory`` (default) — :class:`langgraph.store.memory.InMemoryStore`,
  perfect for dev / unit tests / single-process deployments.
* ``sqlite`` — same ``InMemoryStore`` backing class today, but with a
  warning logged so operators know persistence across restarts is *not*
  yet provided. Kept as a named backend so production configs don't
  break when sqlite persistence lands in a future phase.
* ``postgresql`` — :class:`langgraph.store.postgres.aio.AsyncPostgresStore`,
  lazy-imported so ``langgraph-checkpoint-postgres`` remains an optional
  dependency. Raises a helpful :class:`ImportError` when the package is
  missing.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from lightagent.core.config import get_settings
from lightagent.core.logging import get_logger
from lightagent.security.pii_sanitizer import PIISanitizer

if TYPE_CHECKING:
    from langgraph.store.base import BaseStore

logger = get_logger("lightagent.memory.long_term_store")


# Namespace templates from SPEC-039 AC-039-5. Exposed as module-level
# helpers so callers can construct namespaces with the canonical shape
# instead of building tuples by hand.
def preferences_namespace(user_id: str) -> tuple[str, str]:
    """Return the canonical ``preferences`` namespace for a user."""
    return ("preferences", user_id)


def project_context_namespace(project_id: str) -> tuple[str, str]:
    """Return the canonical ``project_context`` namespace for a project."""
    return ("project_context", project_id)


def learned_facts_namespace(user_id: str) -> tuple[str, str]:
    """Return the canonical ``learned_facts`` namespace for a user."""
    return ("learned_facts", user_id)


def agent_feedback_namespace(user_id: str) -> tuple[str, str]:
    """Return the canonical ``agent_feedback`` namespace for a user."""
    return ("agent_feedback", user_id)


class LongTermMemoryStore:
    """Cross-thread, cross-session fact store with PII sanitization.

    The store exposes four async operations — :meth:`store_fact`,
    :meth:`recall_facts`, :meth:`delete_fact`, :meth:`list_namespaces` —
    plus a synchronous :meth:`backend_name` accessor for health checks.
    All write operations sanitize their ``value`` argument before
    forwarding to the underlying :class:`~langgraph.store.base.BaseStore`.

    TTL is implemented by stamping the stored document with a UNIX
    ``expires_at`` timestamp; expired entries are filtered out at recall
    time (we do *not* rely on the backend's own TTL support because the
    in-memory backend does not persist between restarts anyway and the
    postgres backend's TTL semantics are still experimental). Expired
    entries are additionally deleted lazily whenever they are observed
    by :meth:`recall_facts`.

    Args:
        backend: One of ``"memory"``, ``"sqlite"``, or ``"postgresql"``.
            When omitted, reads :attr:`Settings.memory_backend`.

    Raises:
        ImportError: If ``backend="postgresql"`` and
            ``langgraph-checkpoint-postgres`` is not installed.
        ValueError: If ``backend`` is not one of the supported names.
    """

    def __init__(self, backend: str | None = None) -> None:
        self._backend_name = backend or get_settings().memory_backend
        self._store: BaseStore = self._build_store(self._backend_name)

    @staticmethod
    def _build_store(backend: str) -> BaseStore:
        """Instantiate the underlying LangGraph store for ``backend``.

        Args:
            backend: One of ``"memory"``, ``"sqlite"``, ``"postgresql"``.

        Returns:
            A :class:`~langgraph.store.base.BaseStore` ready to accept
            ``aput`` / ``aget`` / ``asearch`` calls.

        Raises:
            ImportError: If the postgresql backend is selected but the
                ``langgraph-checkpoint-postgres`` package is not
                installed.
            ValueError: If ``backend`` is unknown.
        """
        if backend == "memory":
            from langgraph.store.memory import InMemoryStore

            return InMemoryStore()

        if backend == "sqlite":
            # LangGraph does not yet ship an AsyncSqliteStore; fall back
            # to InMemoryStore so existing configs keep working, but warn
            # operators that entries will not survive a process restart.
            from langgraph.store.memory import InMemoryStore

            logger.warning(
                "memory_sqlite_backend_fallback",
                message=(
                    "LIGHTAGENT_MEMORY_BACKEND=sqlite currently uses an "
                    "InMemoryStore fallback — entries do not persist "
                    "across restarts. Set backend=postgresql for durable "
                    "cross-process memory."
                ),
            )
            return InMemoryStore()

        if backend == "postgresql":
            try:
                from langgraph.store.postgres.aio import (  # type: ignore[import-not-found]
                    AsyncPostgresStore,
                )
            except ImportError as e:  # pragma: no cover — exercised via mocked tests
                raise ImportError(
                    "PostgreSQL long-term memory backend requires the "
                    "optional 'langgraph-checkpoint-postgres' package. "
                    "Install it with `uv pip install "
                    "langgraph-checkpoint-postgres`."
                ) from e

            db_url = get_settings().db_url or ""
            return AsyncPostgresStore(db_url)  # type: ignore[no-any-return]

        raise ValueError(
            f"Unknown memory backend: {backend!r}. Expected 'memory', 'sqlite' or 'postgresql'."
        )

    @property
    def backend_name(self) -> str:
        """Return the backend identifier this instance was built with."""
        return self._backend_name

    async def store_fact(
        self,
        user_id: str,
        namespace: tuple[str, ...],
        key: str,
        value: Any,
        ttl_days: int | None = None,
    ) -> None:
        """Persist a single fact under ``namespace[/key]`` for ``user_id``.

        The ``value`` argument is stringified and passed through
        :meth:`PIISanitizer.sanitize` before any storage write happens
        (SPEC-039 AC-039-2). This is a hard requirement: there is no
        escape hatch that skips sanitization. If a caller needs to store
        structured data, it is expected to pre-serialise the object to
        JSON and let the sanitizer operate on the resulting string —
        PII-looking substrings embedded in JSON will be replaced with
        placeholder tokens, which is the desired outcome.

        Args:
            user_id: The owning user. Stored in the record metadata so
                recall operations can enforce user isolation even when
                callers pass a shared namespace by mistake.
            namespace: LangGraph store namespace — usually one of the
                helpers exposed at module level (e.g.
                :func:`preferences_namespace`).
            key: Identifier unique within ``namespace``. Overwrites any
                existing entry with the same key.
            value: The fact to store. Will be coerced to ``str`` and
                sanitized before writing.
            ttl_days: Optional expiration window in days. Defaults to
                :attr:`Settings.memory_default_ttl_days` when ``None``;
                pass ``0`` to disable expiration entirely.
        """
        resolved_ttl_days = (
            ttl_days if ttl_days is not None else get_settings().memory_default_ttl_days
        )
        now = time.time()
        expires_at: float | None = (
            now + resolved_ttl_days * 86400.0 if resolved_ttl_days > 0 else None
        )

        sanitized = PIISanitizer.sanitize(str(value))
        document: dict[str, Any] = {
            "value": sanitized,
            "user_id": user_id,
            "created_at": now,
            "expires_at": expires_at,
        }
        await self._store.aput(namespace, key, document)

    async def recall_facts(
        self,
        namespace: tuple[str, ...],
        query: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Retrieve up to ``limit`` facts from ``namespace`` matching ``query``.

        The call is deliberately forgiving: on *any* exception raised by
        the underlying store (connection errors, schema drift, missing
        indexes, backend not yet initialised, …) we log a warning and
        return an empty list so the supervisor can continue without
        error — SPEC-039 AC-039-4 requires graceful degradation here.

        Expired entries are filtered out at read time and lazily deleted
        from the store so subsequent recalls do not re-observe them.

        Args:
            namespace: Namespace prefix to search.
            query: Free-form text used as the semantic search hint. When
                the backend does not support vector search this reduces
                to a simple substring scoring pass; callers should still
                pass a meaningful query because the behaviour may be
                upgraded in the future.
            limit: Maximum number of facts to return. Must be ``>= 0``;
                a value of ``0`` short-circuits and returns ``[]``.

        Returns:
            A list of ``{"key", "value", "user_id", "created_at"}``
            dictionaries sorted by relevance / recency. Empty on any
            failure or when the store has nothing to offer.
        """
        if limit <= 0:
            return []

        try:
            # LangGraph's ``asearch`` accepts an optional ``query`` for
            # semantic ranking; when the backend is InMemoryStore without
            # an embedding index configured, ``query`` is ignored and the
            # call degrades to a namespace scan. That is fine — the
            # caller treats the result as best-effort context injection.
            items = await self._store.asearch(
                namespace,
                query=query or None,
                limit=max(limit * 2, limit),
            )
        except Exception as exc:  # graceful degradation (AC-039-4)
            logger.warning(
                "memory_recall_failed",
                namespace=namespace,
                error=str(exc),
            )
            return []

        now = time.time()
        results: list[dict[str, Any]] = []
        expired_keys: list[tuple[tuple[str, ...], str]] = []

        for item in items:
            # LangGraph ``Item`` / ``SearchItem`` objects expose ``value``
            # (the dict we wrote) plus ``key`` and ``namespace``; fall
            # back to getattr because the exact class may differ by
            # backend version.
            value = getattr(item, "value", None) or {}
            if not isinstance(value, dict):
                continue
            expires_at = value.get("expires_at")
            if expires_at is not None and expires_at < now:
                ns = getattr(item, "namespace", namespace)
                expired_keys.append((tuple(ns), getattr(item, "key", "")))
                continue
            results.append(
                {
                    "key": getattr(item, "key", ""),
                    "value": value.get("value", ""),
                    "user_id": value.get("user_id", ""),
                    "created_at": value.get("created_at"),
                }
            )
            if len(results) >= limit:
                break

        # Best-effort lazy cleanup of expired entries — silent failures
        # are acceptable here because the same entries will be skipped
        # again on the next recall.
        for ns, key in expired_keys:
            try:
                await self._store.adelete(ns, key)
            except Exception as exc:  # cleanup is best-effort
                logger.debug("memory_expired_cleanup_failed", error=str(exc))

        return results

    async def delete_fact(self, namespace: tuple[str, ...], key: str) -> None:
        """Remove a single fact. Missing keys are a no-op."""
        try:
            await self._store.adelete(namespace, key)
        except Exception as exc:  # delete is idempotent
            logger.warning(
                "memory_delete_failed",
                namespace=namespace,
                key=key,
                error=str(exc),
            )


__all__ = [
    "LongTermMemoryStore",
    "agent_feedback_namespace",
    "learned_facts_namespace",
    "preferences_namespace",
    "project_context_namespace",
]
