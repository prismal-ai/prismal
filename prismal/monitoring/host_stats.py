"""In-process report recorders for the host read surface (HRS — SPEC-HRS-DEC/RTE/WIN-001).

The host (``prismal-server``) cannot read OTel back, so the engine keeps a small
in-process mirror of the DEC and AMR counters. :func:`record_metric` is tapped
from :meth:`prismal.monitoring.otel.OTelManager.increment_counter` — i.e. fed at
exactly the same points as the OTel instruments — and works with OTel disabled.

Design:

* **1-minute buckets** kept for ``host_surface_stats_retention_h`` (default 168 h);
  older buckets are pruned on write. Each bucket bounds its key cardinality
  (``max_keys_per_bucket``) so a label explosion can never grow memory unbounded.
* **Window clamping** (SPEC-HRS-WIN-001): a query's ``since`` is clamped to the
  later of the recorder start (process start) and the retention horizon — never
  extrapolated; ``until`` is clamped to *now*.
* **Tenant scoping**: a sample's tenant comes from its ``tenant`` / ``org_id``
  attribute when present (non-empty), else from the :data:`current_org`
  ContextVar (bind it with :func:`use_org`). A query with ``org_id=None`` (a
  single-tenant or global admin context) sees **every** sample; a query for a
  tenant sees only that tenant's samples (unscoped samples are not attributed
  to any tenant).
* Thread-safe (one lock per recorder); :meth:`StatsRecorder.record` and
  :func:`record_metric` never raise.

Recorders are in-process only — they reset on restart (persistent reporting is
out of scope; the host persists usage itself).
"""

from __future__ import annotations

import math
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

_DEFAULT_RETENTION_H = 168
_BUCKET_S = 60
_TENANT_KEYS = ("tenant", "org_id")

#: Tenant bound to the current task/thread for samples without a tenant label.
current_org: ContextVar[str | None] = ContextVar("prismal_host_stats_org", default=None)

#: ``(metric, sorted attribute items)`` — the aggregation key.
type StatKey = tuple[str, tuple[tuple[str, str], ...]]


@contextmanager
def use_org(org_id: str | None) -> Iterator[None]:
    """Bind *org_id* to :data:`current_org` for the duration of the block."""
    token = current_org.set(org_id)
    try:
        yield
    finally:
        current_org.reset(token)


@dataclass(frozen=True)
class StatsAggregate:
    """Totals of one query plus the effective (clamped) window."""

    totals: dict[StatKey, float]
    since: datetime
    until: datetime


def _to_ts(value: datetime) -> float:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()


def _default_retention() -> int:
    try:
        from prismal.core.config import get_settings

        return int(get_settings().host_surface_stats_retention_h)
    except Exception:
        return _DEFAULT_RETENTION_H


class StatsRecorder:
    """Bucketed, tenant-tagged counter mirror for a fixed set of metric keys."""

    METRICS: ClassVar[frozenset[str]] = frozenset()

    def __init__(
        self,
        *,
        retention_h: int | None = None,
        clock: Callable[[], float] = time.time,
        max_keys_per_bucket: int = 1024,
    ) -> None:
        self._clock = clock
        self._retention_h = retention_h
        self._max_keys = max_keys_per_bucket
        self._lock = threading.Lock()
        # minute index -> {(org, metric, attrs): total}
        self._buckets: dict[int, dict[tuple[str | None, str, tuple[tuple[str, str], ...]], float]]
        self._buckets = {}
        self._started = clock()

    @property
    def retention_h(self) -> int:
        """Retention horizon in hours (resolved lazily from settings)."""
        if self._retention_h is None:
            self._retention_h = _default_retention()
        return self._retention_h

    def bucket_count(self) -> int:
        """Number of live minute buckets (for tests / introspection)."""
        with self._lock:
            return len(self._buckets)

    def reset(self) -> None:
        """Drop every bucket and restart the clock window (tests)."""
        with self._lock:
            self._buckets.clear()
            self._started = self._clock()

    def record(self, metric: str, value: float, attributes: Mapping[str, Any] | None) -> None:
        """Add *value* to *metric* in the current minute bucket. Never raises."""
        if metric not in self.METRICS:
            return
        try:
            amount = float(value)
            if not math.isfinite(amount):
                return
            attrs = dict(attributes or {})
            org: str | None = None
            for key in _TENANT_KEYS:
                raw = attrs.pop(key, None)
                if org is None and raw:
                    org = str(raw)
            if org is None:
                org = current_org.get()
            items = tuple(sorted((str(k), str(v)) for k, v in attrs.items()))
            now = self._clock()
            minute = int(now // _BUCKET_S)
            horizon = int((now - self.retention_h * 3600) // _BUCKET_S)
            with self._lock:
                for old in [m for m in self._buckets if m < horizon]:
                    del self._buckets[old]
                bucket = self._buckets.setdefault(minute, {})
                full_key = (org, metric, items)
                if full_key not in bucket and len(bucket) >= self._max_keys:
                    return
                bucket[full_key] = bucket.get(full_key, 0.0) + amount
        except Exception:  # observability never breaks the caller
            return

    def aggregate(
        self,
        *,
        since: datetime | None,
        until: datetime | None,
        org_id: str | None,
        retention_h: int | None = None,
    ) -> StatsAggregate:
        """Sum the buckets inside the clamped ``[since, until)`` window for *org_id*."""
        now = self._clock()
        horizon_h = self.retention_h if retention_h is None else min(self.retention_h, retention_h)
        floor = max(self._started, now - horizon_h * 3600)
        lo = floor if since is None else max(_to_ts(since), floor)
        hi = now if until is None else min(_to_ts(until), now)
        totals: dict[StatKey, float] = {}
        with self._lock:
            for minute, bucket in self._buckets.items():
                start = minute * _BUCKET_S
                if start + _BUCKET_S <= lo or start > hi:
                    continue
                for (org, metric, items), amount in bucket.items():
                    if org_id is not None and org != org_id:
                        continue
                    key = (metric, items)
                    totals[key] = totals.get(key, 0.0) + amount
        return StatsAggregate(
            totals=totals,
            since=datetime.fromtimestamp(lo, tz=UTC),
            until=datetime.fromtimestamp(max(lo, hi), tz=UTC),
        )


class DecisionStatsRecorder(StatsRecorder):
    """Mirror of the DEC counters (``decisions``, ``llm_calls_avoided``)."""

    METRICS: ClassVar[frozenset[str]] = frozenset({"decisions", "llm_calls_avoided"})


class RoutingStatsRecorder(StatsRecorder):
    """Mirror of the AMR routing counters (SPEC-AMR-OTEL-001)."""

    METRICS: ClassVar[frozenset[str]] = frozenset(
        {"routing_decisions", "routing_escalations", "plan_cache_hits", "routing_savings_usd"}
    )


#: Every counter key the OTel tap forwards here.
HOST_STATS_METRICS: frozenset[str] = DecisionStatsRecorder.METRICS | RoutingStatsRecorder.METRICS

_DECISIONS = DecisionStatsRecorder()
_ROUTING = RoutingStatsRecorder()


def get_decision_stats_recorder() -> DecisionStatsRecorder:
    """The process :class:`DecisionStatsRecorder`."""
    return _DECISIONS


def get_routing_stats_recorder() -> RoutingStatsRecorder:
    """The process :class:`RoutingStatsRecorder`."""
    return _ROUTING


def record_metric(metric: str, value: float, attributes: Mapping[str, Any] | None) -> None:
    """Forward one counter increment to the matching process recorder. Never raises."""
    try:
        if metric in DecisionStatsRecorder.METRICS:
            _DECISIONS.record(metric, value, attributes)
        elif metric in RoutingStatsRecorder.METRICS:
            _ROUTING.record(metric, value, attributes)
    except Exception:
        return


__all__ = [
    "HOST_STATS_METRICS",
    "DecisionStatsRecorder",
    "RoutingStatsRecorder",
    "StatsAggregate",
    "StatsRecorder",
    "current_org",
    "get_decision_stats_recorder",
    "get_routing_stats_recorder",
    "record_metric",
    "use_org",
]
