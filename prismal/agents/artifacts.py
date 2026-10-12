"""Tenant/run-scoped artifact store (Phase TKE — SPEC-TKE-ART-001).

Large tool outputs, handoff payloads and RAG blobs are parked here instead of
in ``state["messages"]``; the model sees a compact :class:`ArtifactRef` and may
page through the content with the ``read_artifact`` tool.

Security properties:

* ids are unguessable (``secrets.token_urlsafe(16)``);
* every read is bound to the ``(tenant, run_id)`` that stored the artifact — a
  mismatch raises :class:`~prismal.core.exceptions.ArtifactNotFound`, exactly
  like a missing id, and is audited hash-first (never content);
* content read back goes through the same sanitize path as the original tool
  output (``InputSanitizer`` by default).

Two adapters conform to :class:`ArtifactStorePort`: :class:`InMemoryArtifactStore`
(default) and :class:`SqliteArtifactStore` (stdlib ``sqlite3``).
"""

from __future__ import annotations

import contextlib
import hashlib
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from prismal.core.config import get_settings
from prismal.core.exceptions import ArtifactNotFound, ArtifactStoreError
from prismal.utils.token_estimate import estimate_tokens

if TYPE_CHECKING:
    from collections.abc import Callable

    from langchain_core.tools import BaseTool

    from prismal.core.config import Settings

#: String returned by ``read_artifact`` when an id is missing / out of scope.
NOT_FOUND_MESSAGE = "artifact not found"


@dataclass(frozen=True)
class ArtifactRef:
    """Compact, model-visible handle to a stored artifact."""

    id: str
    run_id: str
    tenant: str
    tool: str | None
    tokens_estimate: int
    created_at: float
    kind: str


@dataclass(frozen=True)
class ArtifactSlice:
    """A ``[start, end)`` character window over an artifact's content."""

    ref: ArtifactRef
    content: str
    start: int
    end: int
    total_chars: int


@runtime_checkable
class ArtifactStorePort(Protocol):
    """Port for tenant/run-scoped artifact storage (SPEC-TKE-ART-001)."""

    def put(
        self,
        content: str,
        *,
        run_id: str,
        tenant: str,
        kind: str,
        tool: str | None = None,
        ttl_s: int | None = None,
    ) -> ArtifactRef:
        """Store *content* and return its reference."""
        ...

    def get(
        self,
        artifact_id: str,
        *,
        run_id: str,
        tenant: str,
        start: int = 0,
        end: int | None = None,
    ) -> ArtifactSlice:
        """Return a slice of the artifact; raise ``ArtifactNotFound`` if absent/out of scope."""
        ...

    def purge_expired(self, now: float | None = None) -> int:
        """Delete expired artifacts and return how many were removed."""
        ...


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _default_audit() -> Any:
    from prismal.security import audit as audit_mod

    return audit_mod.AuditLogger()


def _increment(metric: str, attributes: dict[str, Any] | None = None) -> None:
    with contextlib.suppress(Exception):
        from prismal.monitoring import otel

        otel.OTelManager().increment_counter(metric, 1, attributes or {})


@dataclass(frozen=True)
class _Record:
    ref: ArtifactRef
    content: str
    expires_at: float | None


class _BaseArtifactStore:
    """Shared put/get/scope/TTL logic; subclasses supply persistence."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        clock: Callable[[], float] = time.time,
        audit: Any | None = None,
    ) -> None:
        self._settings = settings if settings is not None else get_settings()
        self._clock = clock
        self._audit = audit

    # -- persistence hooks -------------------------------------------------
    def _save(self, record: _Record) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def _load(self, artifact_id: str) -> _Record | None:  # pragma: no cover - abstract
        raise NotImplementedError

    def _delete_expired(self, now: float) -> int:  # pragma: no cover - abstract
        raise NotImplementedError

    # -- audit ---------------------------------------------------------------
    def _log(self, event_type: str, payload: dict[str, object]) -> None:
        try:
            if self._audit is None:
                self._audit = _default_audit()
            self._audit.log_event(event_type, payload)
        except Exception:  # audit must never break a call
            return

    # -- port ------------------------------------------------------------------
    def put(
        self,
        content: str,
        *,
        run_id: str,
        tenant: str,
        kind: str,
        tool: str | None = None,
        ttl_s: int | None = None,
    ) -> ArtifactRef:
        """Store *content* scoped to ``(tenant, run_id)``; ``ttl_s=0`` never expires."""
        ttl = self._settings.artifact_ttl_s if ttl_s is None else ttl_s
        if ttl < 0:
            raise ValueError("ttl_s must be >= 0")
        now = self._clock()
        ref = ArtifactRef(
            id=secrets.token_urlsafe(16),
            run_id=run_id,
            tenant=tenant,
            tool=tool,
            tokens_estimate=estimate_tokens(content),
            created_at=now,
            kind=kind,
        )
        self._save(_Record(ref=ref, content=content, expires_at=now + ttl if ttl else None))
        _increment("artifacts_stored", {"kind": kind})
        return ref

    def get(
        self,
        artifact_id: str,
        *,
        run_id: str,
        tenant: str,
        start: int = 0,
        end: int | None = None,
    ) -> ArtifactSlice:
        """Return ``content[start:end]`` (clamped) when the caller's scope matches."""
        record = self._load(artifact_id)
        if (
            record is not None
            and record.expires_at is not None
            and self._clock() >= record.expires_at
        ):
            record = None
        if record is None:
            raise ArtifactNotFound(f"artifact not found: {artifact_id}")
        if record.ref.tenant != tenant or record.ref.run_id != run_id:
            self._log(
                "artifact_scope_mismatch",
                {
                    "artifact_id_hash": _hash(artifact_id),
                    "requested_tenant_hash": _hash(tenant),
                    "requested_run_hash": _hash(run_id),
                },
            )
            raise ArtifactNotFound(f"artifact not found: {artifact_id}")
        total = len(record.content)
        lo = min(max(start, 0), total)
        hi = total if end is None else min(max(end, lo), total)
        _increment("artifact_reads", {"kind": record.ref.kind})
        return ArtifactSlice(
            ref=record.ref,
            content=record.content[lo:hi],
            start=lo,
            end=hi,
            total_chars=total,
        )

    def purge_expired(self, now: float | None = None) -> int:
        """Delete artifacts whose TTL elapsed at *now* (default: the clock)."""
        return self._delete_expired(self._clock() if now is None else now)


class InMemoryArtifactStore(_BaseArtifactStore):
    """Process-local artifact store (default backend)."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        clock: Callable[[], float] = time.time,
        audit: Any | None = None,
    ) -> None:
        super().__init__(settings=settings, clock=clock, audit=audit)
        self._records: dict[str, _Record] = {}
        self._lock = threading.Lock()

    def _save(self, record: _Record) -> None:
        with self._lock:
            self._records[record.ref.id] = record

    def _load(self, artifact_id: str) -> _Record | None:
        with self._lock:
            return self._records.get(artifact_id)

    def _delete_expired(self, now: float) -> int:
        with self._lock:
            expired = [
                k
                for k, r in self._records.items()
                if r.expires_at is not None and now >= r.expires_at
            ]
            for k in expired:
                del self._records[k]
            return len(expired)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    tenant TEXT NOT NULL,
    tool TEXT,
    kind TEXT NOT NULL,
    tokens_estimate INTEGER NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL,
    content TEXT NOT NULL
)
"""


class SqliteArtifactStore(_BaseArtifactStore):
    """Durable artifact store backed by stdlib ``sqlite3``."""

    def __init__(
        self,
        path: str,
        *,
        settings: Settings | None = None,
        clock: Callable[[], float] = time.time,
        audit: Any | None = None,
    ) -> None:
        super().__init__(settings=settings, clock=clock, audit=audit)
        self._path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute(_SCHEMA)

    def _connect(self) -> contextlib.closing[sqlite3.Connection]:
        return contextlib.closing(sqlite3.connect(self._path))

    def _save(self, record: _Record) -> None:
        ref = record.ref
        with self._lock, self._connect() as conn, conn:
            conn.execute(
                "INSERT INTO artifacts (id, run_id, tenant, tool, kind, tokens_estimate,"
                " created_at, expires_at, content) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ref.id,
                    ref.run_id,
                    ref.tenant,
                    ref.tool,
                    ref.kind,
                    ref.tokens_estimate,
                    ref.created_at,
                    record.expires_at,
                    record.content,
                ),
            )

    def _load(self, artifact_id: str) -> _Record | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT id, run_id, tenant, tool, kind, tokens_estimate, created_at,"
                " expires_at, content FROM artifacts WHERE id = ?",
                (artifact_id,),
            ).fetchone()
        if row is None:
            return None
        ref = ArtifactRef(
            id=row[0],
            run_id=row[1],
            tenant=row[2],
            tool=row[3],
            kind=row[4],
            tokens_estimate=int(row[5]),
            created_at=float(row[6]),
        )
        return _Record(ref=ref, content=row[8], expires_at=row[7])

    def _delete_expired(self, now: float) -> int:
        with self._lock, self._connect() as conn, conn:
            cur = conn.execute(
                "DELETE FROM artifacts WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (now,),
            )
            return int(cur.rowcount)


def build_artifact_store(settings: Settings | None = None) -> ArtifactStorePort:
    """Build the artifact store selected by ``settings.artifact_store_backend``."""
    resolved = settings if settings is not None else get_settings()
    backend = resolved.artifact_store_backend
    if backend == "memory":
        return InMemoryArtifactStore(settings=resolved)
    if backend == "sqlite":
        return SqliteArtifactStore(resolved.artifact_store_path, settings=resolved)
    raise ArtifactStoreError(f"unknown artifact store backend: {backend!r}")


# One store per backend (and SQLite path) per process: the compactor, the
# tool-output budget and ``read_artifact`` must see the same artifacts. Runs stay
# isolated by the (tenant, run_id) scope every get/put carries.
_SHARED_STORES: dict[tuple[str, str], ArtifactStorePort] = {}
_SHARED_LOCK = threading.Lock()


def shared_artifact_store(settings: Settings | None = None) -> ArtifactStorePort:
    """Return the process-wide store for ``settings.artifact_store_backend``."""
    resolved = settings if settings is not None else get_settings()
    path = resolved.artifact_store_path if resolved.artifact_store_backend == "sqlite" else ""
    key = (resolved.artifact_store_backend, path)
    with _SHARED_LOCK:
        store = _SHARED_STORES.get(key)
        if store is None:
            store = build_artifact_store(resolved)
            _SHARED_STORES[key] = store
        return store


def anonymous_run_id() -> str:
    """A fresh, unguessable run scope for callers without a ``session_id``.

    Never a shared constant: two anonymous runs must not see each other's
    artifacts, even inside the same tenant.
    """
    return f"anon-{secrets.token_hex(8)}"


def make_read_artifact_tool(
    store: ArtifactStorePort,
    *,
    run_id: str,
    tenant: str,
    sanitize: Callable[[str], str] | None = None,
) -> BaseTool:
    """Return a ``read_artifact`` tool bound to one ``(tenant, run_id)`` scope.

    Content is passed through *sanitize* (default ``InputSanitizer().sanitize``)
    — the same path as the original tool output. Missing or out-of-scope ids
    yield ``"artifact not found"`` instead of raising into the agent loop.
    """
    from langchain_core.tools import StructuredTool

    if sanitize is None:
        from prismal.security.sanitizer import InputSanitizer

        sanitize = InputSanitizer().sanitize
    clean = sanitize

    def read_artifact(artifact_id: str, start: int = 0, end: int | None = None) -> str:
        """Read a character window [start, end) of a stored artifact by id."""
        try:
            sl = store.get(artifact_id, run_id=run_id, tenant=tenant, start=start, end=end)
        except ArtifactNotFound:
            return NOT_FOUND_MESSAGE
        return clean(sl.content)

    return StructuredTool.from_function(
        func=read_artifact,
        name="read_artifact",
        description=(
            "Read part of a stored artifact (large tool output) by its id. "
            "Use start/end character offsets to page through long content."
        ),
    )


__all__ = [
    "NOT_FOUND_MESSAGE",
    "ArtifactRef",
    "ArtifactSlice",
    "ArtifactStorePort",
    "InMemoryArtifactStore",
    "SqliteArtifactStore",
    "anonymous_run_id",
    "build_artifact_store",
    "make_read_artifact_tool",
    "shared_artifact_store",
]
