"""Flat-fee quota ledger (Phase GW — SPEC-GW-QUOTA-001; GW3-01, GW3-08).

Flat-fee gateways expose no quota API (OpenCode Go — VERIFY.md GW0-05) or a
per-day request cap on free models (OpenRouter ``:free`` — GW0-02), so prismal
tracks usage locally and refuses (or downgrades) *before* the network call.

Windows:

* ``opencode-go`` with ``opencode_go_monthly_cap_usd > 0``: rolling ``5h``
  (cap * ``opencode_go_window_5h_ratio``), ``week`` (cap *
  ``opencode_go_window_week_ratio``) and ``month`` (cap, rolling 30 days), in
  usd-equivalent.
* ``openrouter`` with a ``:free`` model: one ``day`` window of
  ``OPENROUTER_FREE_DAILY_LIMIT`` requests, reset at UTC midnight, shared by
  every ``:free`` model of the account.

The ledger is scoped per ``(org_id, gateway)`` and is fail-open: an internal
failure allows the call and counts ``gateway_errors{kind="ledger"}``.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from prismal.core.logging import get_logger
from prismal.providers.gateways.base import QuotaSnapshot
from prismal.providers.gateways.openai_compatible import count

if TYPE_CHECKING:
    from collections.abc import Callable

    from prismal.core.config import Settings

logger = get_logger("prismal.providers.gateways.quota")

# OpenRouter ``:free`` models: 50 requests/day below $10 of purchased credits.
# Accounts with ≥ $10 credits get 1,000/day (VERIFY.md GW0-02); the ledger keeps
# the conservative tier — the remote ``free_model_daily_requests`` is authoritative.
OPENROUTER_FREE_DAILY_LIMIT = 50

OPENCODE_GO = "opencode-go"
OPENROUTER = "openrouter"
_FREE_SUFFIX = ":free"

WindowName = Literal["5h", "week", "month", "day"]
QuotaKey = tuple[str | None, str]  # (org_id, gateway)

_ROLLING: dict[str, timedelta] = {
    "5h": timedelta(hours=5),
    "week": timedelta(days=7),
    "month": timedelta(days=30),
}
_RETENTION = max(_ROLLING.values())


def utcnow() -> datetime:
    """Timezone-aware current UTC time (the default ledger clock)."""
    return datetime.now(UTC)


@dataclass(frozen=True)
class QuotaWindow:
    """One quota window (SPEC-GW-QUOTA-001)."""

    name: WindowName
    limit: float  # usd-equivalent or requests
    unit: Literal["usd", "requests"]
    rolling: bool


@dataclass(frozen=True)
class QuotaVerdict:
    """Preflight outcome; ``allowed`` is True only for ``action="allow"``."""

    allowed: bool
    action: Literal["allow", "refuse", "downgrade"]
    exhausted_window: str | None
    downgrade_model: str | None  # a ``gw:`` reference when ``action="downgrade"``


_ALLOW = QuotaVerdict(allowed=True, action="allow", exhausted_window=None, downgrade_model=None)


@dataclass(frozen=True)
class QuotaEvent:
    """One recorded call."""

    at: datetime
    model: str
    equivalent_usd: float
    requests: int


@runtime_checkable
class QuotaStore(Protocol):
    """Persistence port for ledger events, keyed by ``(org_id, gateway)``."""

    def add(self, key: QuotaKey, event: QuotaEvent) -> None: ...

    def events(self, key: QuotaKey) -> list[QuotaEvent]: ...

    def prune(self, key: QuotaKey, before: datetime) -> None: ...


class InMemoryQuotaStore:
    """Process-local default store (thread-safe)."""

    def __init__(self) -> None:
        self._events: dict[QuotaKey, list[QuotaEvent]] = {}
        self._lock = threading.Lock()

    def add(self, key: QuotaKey, event: QuotaEvent) -> None:
        with self._lock:
            self._events.setdefault(key, []).append(event)

    def events(self, key: QuotaKey) -> list[QuotaEvent]:
        with self._lock:
            return list(self._events.get(key, ()))

    def prune(self, key: QuotaKey, before: datetime) -> None:
        with self._lock:
            kept = [e for e in self._events.get(key, ()) if e.at >= before]
            if kept:
                self._events[key] = kept
            else:
                self._events.pop(key, None)


def _is_free(model: str) -> bool:
    return model.endswith(_FREE_SUFFIX)


class QuotaLedger:
    """Local usage ledger for flat-fee windows (SPEC-GW-QUOTA-001)."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        org_id: str | None = None,
        clock: Callable[[], datetime] = utcnow,
        store: QuotaStore | None = None,
    ) -> None:
        if settings is None:
            from prismal.core.config import get_settings

            settings = get_settings()
        self._settings = settings
        self.org_id = org_id
        self._clock = clock
        self._store: QuotaStore = store if store is not None else InMemoryQuotaStore()

    # ── windows ──────────────────────────────────────────────────────────────

    def windows_for(self, gateway: str, model: str) -> tuple[QuotaWindow, ...]:
        """The windows that apply to one call target."""
        if gateway == OPENROUTER and not _is_free(model):
            return ()
        return self._gateway_windows(gateway)

    def _gateway_windows(self, gateway: str) -> tuple[QuotaWindow, ...]:
        s = self._settings
        if gateway == OPENCODE_GO:
            cap = float(s.opencode_go_monthly_cap_usd)
            if cap <= 0:
                return ()
            return (
                QuotaWindow("5h", cap * s.opencode_go_window_5h_ratio, "usd", rolling=True),
                QuotaWindow("week", cap * s.opencode_go_window_week_ratio, "usd", rolling=True),
                QuotaWindow("month", cap, "usd", rolling=True),
            )
        if gateway == OPENROUTER:
            return (QuotaWindow("day", OPENROUTER_FREE_DAILY_LIMIT, "requests", rolling=False),)
        return ()

    # ── public API ───────────────────────────────────────────────────────────

    def preflight(self, gateway: str, model: str, *, est_equivalent_usd: float) -> QuotaVerdict:
        """Would this call exceed any window? Fail-open on internal errors."""
        try:
            windows = self.windows_for(gateway, model)
            if not windows:
                return _ALLOW
            events = self._store.events(self._key(gateway))
            now = self._clock()
            for window in windows:
                used = self._used(window, events, now)
                if self._exceeds(window, used, est_equivalent_usd):
                    return self._deny(gateway, window)
            return _ALLOW
        except Exception as exc:
            self._failed(gateway, "preflight", exc)
            return _ALLOW

    def record(self, gateway: str, model: str, *, equivalent_usd: float, requests: int = 1) -> None:
        """Record one call's usage (only for windowed targets); never raises."""
        try:
            if not self.windows_for(gateway, model):
                return
            now = self._clock()
            key = self._key(gateway)
            self._store.prune(key, now - _RETENTION)
            self._store.add(key, QuotaEvent(now, model, float(equivalent_usd), int(requests)))
        except Exception as exc:
            self._failed(gateway, "record", exc)

    def snapshot(self, gateway: str) -> list[QuotaSnapshot]:
        """Ledger view of every window of *gateway* (``source="ledger"``)."""
        try:
            windows = self._gateway_windows(gateway)
            if not windows:
                return []
            events = self._store.events(self._key(gateway))
            now = self._clock()
            return [self._snapshot(gateway, w, events, now) for w in windows]
        except Exception as exc:
            self._failed(gateway, "snapshot", exc)
            return []

    # ── internals ────────────────────────────────────────────────────────────

    def _key(self, gateway: str) -> QuotaKey:
        return (self.org_id, gateway)

    @staticmethod
    def _start(window: QuotaWindow, now: datetime) -> datetime:
        if window.rolling:
            return now - _ROLLING[window.name]
        return now.replace(hour=0, minute=0, second=0, microsecond=0)  # UTC calendar day

    def _in_window(
        self, window: QuotaWindow, events: list[QuotaEvent], now: datetime
    ) -> list[QuotaEvent]:
        start = self._start(window, now)
        # Rolling: an event exactly ``duration`` old has aged out. Calendar: from midnight.
        selected = [e for e in events if (e.at > start if window.rolling else e.at >= start)]
        if window.unit == "requests":  # the OpenRouter free cap counts :free calls only
            selected = [e for e in selected if _is_free(e.model)]
        return selected

    def _used(self, window: QuotaWindow, events: list[QuotaEvent], now: datetime) -> float:
        selected = self._in_window(window, events, now)
        if window.unit == "requests":
            return float(sum(e.requests for e in selected))
        return sum(e.equivalent_usd for e in selected)

    @staticmethod
    def _exceeds(window: QuotaWindow, used: float, est_usd: float) -> bool:
        if window.unit == "requests":
            return used + 1 > window.limit
        # An already-exhausted window refuses even with a zero estimate.
        return used >= window.limit or used + max(est_usd, 0.0) > window.limit

    def _deny(self, gateway: str, window: QuotaWindow) -> QuotaVerdict:
        s = self._settings
        if (
            gateway == OPENCODE_GO
            and s.opencode_go_quota_action == "downgrade"
            and s.opencode_go_downgrade_model
        ):
            return QuotaVerdict(
                allowed=False,
                action="downgrade",
                exhausted_window=window.name,
                downgrade_model=s.opencode_go_downgrade_model,
            )
        return QuotaVerdict(
            allowed=False, action="refuse", exhausted_window=window.name, downgrade_model=None
        )

    def _snapshot(
        self, gateway: str, window: QuotaWindow, events: list[QuotaEvent], now: datetime
    ) -> QuotaSnapshot:
        selected = self._in_window(window, events, now)
        used = self._used(window, events, now)
        resets_at: datetime | None
        if not window.rolling:
            resets_at = self._start(window, now) + timedelta(days=1)
        elif selected:
            resets_at = min(e.at for e in selected) + _ROLLING[window.name]
        else:
            resets_at = None
        return QuotaSnapshot(
            gateway=gateway,
            window=window.name,
            unit=window.unit,
            limit=window.limit,
            used=used,
            remaining=max(window.limit - used, 0.0),
            resets_at=resets_at,
            source="ledger",
        )

    def _failed(self, gateway: str, op: str, exc: Exception) -> None:
        count("gateway_errors", gateway=gateway, kind="ledger")
        logger.warning("gateway.ledger_failed", gateway=gateway, op=op, error=type(exc).__name__)


__all__ = [
    "OPENROUTER_FREE_DAILY_LIMIT",
    "InMemoryQuotaStore",
    "QuotaEvent",
    "QuotaLedger",
    "QuotaStore",
    "QuotaVerdict",
    "QuotaWindow",
    "utcnow",
]
