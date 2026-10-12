"""Shared tenant quota counters (DELTA-2026-10-shared-tenant-quota).

With ``budget_scope="tenant"`` a run's guard checks the tenant's usage over a
UTC calendar window (``budget_tenant_window``: ``month`` | ``day``) instead of
the run's own. Totals live in a :class:`TenantQuotaStorePort`. The SQLite store
(``budget_tenant_store_path``) is shared by every process that uses the same
file; increments are atomic upserts. With no path, totals stay in process memory.

:class:`TenantCostMeter` adds each call's delta to the store and adopts the
returned total, so another process's spend is visible at the next recorded
call — no extra read on the hot path. A store failure falls back to local
accumulation for that call and never raises into the graph.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from prismal.budget.meter import CostMeter
from prismal.budget.types import Usage
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from datetime import datetime

    from prismal.core.config import Settings

logger = get_logger(__name__)

TenantWindow = Literal["month", "day"]

_COLUMNS = ("prompt_tokens", "completion_tokens", "calls", "cost_usd", "wall_clock_s")
_UPSERT = (
    "INSERT INTO budget_tenant_quota (org_id, window, prompt_tokens,"
    " completion_tokens, calls, cost_usd, wall_clock_s)"
    " VALUES (?, ?, ?, ?, ?, ?, ?)"
    " ON CONFLICT (org_id, window) DO UPDATE SET"
    " prompt_tokens = prompt_tokens + excluded.prompt_tokens,"
    " completion_tokens = completion_tokens + excluded.completion_tokens,"
    " calls = calls + excluded.calls,"
    " cost_usd = cost_usd + excluded.cost_usd,"
    " wall_clock_s = wall_clock_s + excluded.wall_clock_s"
    " RETURNING prompt_tokens, completion_tokens, calls, cost_usd, wall_clock_s"
)
_SELECT = (
    "SELECT prompt_tokens, completion_tokens, calls, cost_usd, wall_clock_s"
    " FROM budget_tenant_quota WHERE org_id = ? AND window = ?"
)


def window_key(now: datetime, kind: str) -> str:
    """The UTC calendar window label: ``"2026-10"`` (month) or ``"2026-10-12"`` (day)."""
    return now.strftime("%Y-%m-%d" if kind == "day" else "%Y-%m")


def _quota_usage(usage: Usage) -> Usage:
    """Project *usage* onto the four guarded dimensions."""
    return Usage(
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        calls=usage.calls,
        cost_usd=usage.cost_usd,
        wall_clock_s=usage.wall_clock_s,
    )


@runtime_checkable
class TenantQuotaStorePort(Protocol):
    """Window totals per tenant; ``add`` is an atomic increment returning the new total."""

    def add(self, org_id: str, window: str, usage: Usage) -> Usage:
        """Add *usage* to ``(org_id, window)`` and return the new total."""
        ...

    def get(self, org_id: str, window: str) -> Usage:
        """The total of ``(org_id, window)`` (zero when unseen)."""
        ...


class InMemoryTenantQuotaStore:
    """Process-local store (thread-safe); the default without a store path."""

    def __init__(self) -> None:
        self._totals: dict[tuple[str, str], Usage] = {}
        self._lock = threading.Lock()

    def add(self, org_id: str, window: str, usage: Usage) -> Usage:
        """Add *usage* to ``(org_id, window)`` and return the new total."""
        with self._lock:
            key = (org_id, window)
            total = self._totals.get(key, Usage()) + _quota_usage(usage)
            self._totals[key] = _quota_usage(total)
            return self._totals[key]

    def get(self, org_id: str, window: str) -> Usage:
        """The total of ``(org_id, window)`` (zero when unseen)."""
        with self._lock:
            return self._totals.get((org_id, window), Usage())


class SqliteTenantQuotaStore:
    """SQLite store shared by every process using the same file (WAL, atomic upserts)."""

    def __init__(self, path: str | Path, *, timeout_s: float = 5.0) -> None:
        self._path = str(path)
        self._timeout_s = timeout_s
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS budget_tenant_quota ("
                " org_id TEXT NOT NULL, window TEXT NOT NULL,"
                " prompt_tokens INTEGER NOT NULL DEFAULT 0,"
                " completion_tokens INTEGER NOT NULL DEFAULT 0,"
                " calls INTEGER NOT NULL DEFAULT 0,"
                " cost_usd REAL NOT NULL DEFAULT 0,"
                " wall_clock_s REAL NOT NULL DEFAULT 0,"
                " PRIMARY KEY (org_id, window))"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=self._timeout_s, isolation_level=None)
        conn.execute(f"PRAGMA busy_timeout={int(self._timeout_s * 1000)}")
        return conn

    @staticmethod
    def _usage(row: Any) -> Usage:
        if row is None:
            return Usage()
        prompt, completion, calls, cost, wall = row
        return Usage(
            prompt_tokens=int(prompt),
            completion_tokens=int(completion),
            calls=int(calls),
            cost_usd=float(cost),
            wall_clock_s=float(wall),
        )

    def add(self, org_id: str, window: str, usage: Usage) -> Usage:
        """Atomically add *usage* to ``(org_id, window)`` and return the new total."""
        delta = _quota_usage(usage)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                _UPSERT,
                (
                    org_id,
                    window,
                    delta.prompt_tokens,
                    delta.completion_tokens,
                    delta.calls,
                    delta.cost_usd,
                    delta.wall_clock_s,
                ),
            ).fetchone()
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return self._usage(row)

    def get(self, org_id: str, window: str) -> Usage:
        """The total of ``(org_id, window)`` (zero when unseen)."""
        conn = self._connect()
        try:
            row = conn.execute(
                _SELECT,
                (org_id, window),
            ).fetchone()
        finally:
            conn.close()
        return self._usage(row)


class TenantCostMeter(CostMeter):
    """A :class:`CostMeter` whose ``usage`` is the tenant's window total (TQ-001)."""

    def __init__(
        self,
        *,
        store: TenantQuotaStorePort,
        org_id: str,
        window: str,
        **kwargs: Any,
    ) -> None:
        """Initialize from the store's current window total (zero when unreachable)."""
        super().__init__(tenant=org_id, **kwargs)
        self._store = store
        self._org_id = org_id
        self._window = window
        try:
            self._usage = store.get(org_id, window)
        except Exception as exc:
            logger.warning("budget_tenant_quota_store_failed", error=type(exc).__name__)

    def record(
        self,
        usage: Usage,
        *,
        agent: str | None = None,
        pattern: str | None = None,
        model: str | None = None,
    ) -> Usage:
        """Attribute the call (OTel/FinOps), then adopt the tenant's new window total."""
        local = super().record(usage, agent=agent, pattern=pattern, model=model)
        try:
            total = self._store.add(self._org_id, self._window, usage)
        except Exception as exc:  # TQ-005: never raise into the graph
            logger.warning("budget_tenant_quota_store_failed", error=type(exc).__name__)
            return local
        self._usage = replace(local, **{name: getattr(total, name) for name in _COLUMNS})
        return self._usage


_STORES: dict[str, TenantQuotaStorePort] = {}
_INJECTED: TenantQuotaStorePort | None = None
_STORES_LOCK = threading.Lock()


def set_tenant_quota_store(store: TenantQuotaStorePort | None) -> None:
    """Inject the store every tenant-scoped run uses (hosts/tests); ``None`` clears it."""
    global _INJECTED
    _INJECTED = store


def get_tenant_quota_store(settings: Settings) -> TenantQuotaStorePort:
    """The injected store, else one per ``budget_tenant_store_path`` ("" ⇒ in memory)."""
    if _INJECTED is not None:
        return _INJECTED
    path = settings.budget_tenant_store_path
    with _STORES_LOCK:
        store = _STORES.get(path)
        if store is None:
            store = SqliteTenantQuotaStore(path) if path else InMemoryTenantQuotaStore()
            _STORES[path] = store
        return store


__all__ = [
    "InMemoryTenantQuotaStore",
    "SqliteTenantQuotaStore",
    "TenantCostMeter",
    "TenantQuotaStorePort",
    "TenantWindow",
    "get_tenant_quota_store",
    "set_tenant_quota_store",
    "window_key",
]
