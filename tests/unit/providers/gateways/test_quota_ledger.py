"""Flat-fee quota ledger (Phase GW — SPEC-GW-QUOTA-001, SPEC-GW-OR-001 ``:free``; GW3-01/08)."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from prismal.providers.gateways.quota import (
    OPENROUTER_FREE_DAILY_LIMIT,
    InMemoryQuotaStore,
    QuotaLedger,
    QuotaStore,
    QuotaVerdict,
    QuotaWindow,
)

from .conftest import gw_settings

T0 = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw: float) -> None:
        self.now += timedelta(**kw)


def _go_settings(**kw: Any) -> Any:
    base: dict[str, Any] = {"opencode_go_enabled": True, "opencode_go_monthly_cap_usd": 10.0}
    base.update(kw)
    return gw_settings(**base)


def _ledger(clock: FakeClock | None = None, org_id: str | None = "acme", **kw: Any) -> QuotaLedger:
    return QuotaLedger(settings=_go_settings(**kw), org_id=org_id, clock=clock or FakeClock())


# ── value objects ────────────────────────────────────────────────────────────


def test_value_objects_are_frozen() -> None:
    w = QuotaWindow(name="5h", limit=2.0, unit="usd", rolling=True)
    v = QuotaVerdict(allowed=True, action="allow", exhausted_window=None, downgrade_model=None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        w.limit = 3.0  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        v.allowed = False  # type: ignore[misc]


def test_in_memory_store_satisfies_protocol() -> None:
    assert isinstance(InMemoryQuotaStore(), QuotaStore)


# ── windows ──────────────────────────────────────────────────────────────────


def test_go_windows_derived_from_cap_and_ratios() -> None:
    windows = {w.name: w for w in _ledger().windows_for("opencode-go", "glm-5.3")}
    assert set(windows) == {"5h", "week", "month"}
    assert windows["month"].limit == pytest.approx(10.0)
    assert windows["5h"].limit == pytest.approx(2.0)
    assert windows["week"].limit == pytest.approx(5.0)
    assert all(w.unit == "usd" and w.rolling for w in windows.values())


def test_go_custom_ratios() -> None:
    ledger = _ledger(opencode_go_window_5h_ratio=0.1, opencode_go_window_week_ratio=0.4)
    windows = {w.name: w.limit for w in ledger.windows_for("opencode-go", "glm-5.3")}
    assert windows == pytest.approx({"5h": 1.0, "week": 4.0, "month": 10.0})


def test_go_cap_zero_means_no_windows() -> None:
    assert _ledger(opencode_go_monthly_cap_usd=0.0).windows_for("opencode-go", "glm-5.3") == ()


def test_openrouter_free_model_has_daily_request_window() -> None:
    (window,) = _ledger().windows_for("openrouter", "deepseek/deepseek-r1:free")
    assert window == QuotaWindow(
        name="day", limit=OPENROUTER_FREE_DAILY_LIMIT, unit="requests", rolling=False
    )
    assert OPENROUTER_FREE_DAILY_LIMIT == 50


def test_openrouter_paid_model_and_other_gateways_have_no_windows() -> None:
    ledger = _ledger()
    assert ledger.windows_for("openrouter", "anthropic/claude-sonnet-4.5") == ()
    assert ledger.windows_for("opencode", "claude-sonnet-4-5") == ()
    assert ledger.windows_for("omniroute", "x:free") == ()


# ── preflight / record arithmetic ────────────────────────────────────────────


def test_preflight_allows_under_limit() -> None:
    ledger = _ledger()
    ledger.record("opencode-go", "glm-5.3", equivalent_usd=1.0)
    v = ledger.preflight("opencode-go", "glm-5.3", est_equivalent_usd=0.5)
    assert v == QuotaVerdict(
        allowed=True, action="allow", exhausted_window=None, downgrade_model=None
    )


def test_preflight_refuses_when_estimate_would_exceed_5h() -> None:
    ledger = _ledger()
    ledger.record("opencode-go", "glm-5.3", equivalent_usd=1.8)
    v = ledger.preflight("opencode-go", "glm-5.3", est_equivalent_usd=0.5)
    assert v.allowed is False
    assert v.action == "refuse"
    assert v.exhausted_window == "5h"
    assert v.downgrade_model is None


def test_preflight_refuses_exhausted_window_with_zero_estimate() -> None:
    ledger = _ledger()
    ledger.record("opencode-go", "glm-5.3", equivalent_usd=2.0)
    assert ledger.preflight("opencode-go", "glm-5.3", est_equivalent_usd=0.0).action == "refuse"


def test_5h_window_rolls_off() -> None:
    clock = FakeClock()
    ledger = _ledger(clock)
    ledger.record("opencode-go", "glm-5.3", equivalent_usd=2.0)
    clock.advance(hours=4, minutes=59)
    assert ledger.preflight("opencode-go", "glm-5.3", est_equivalent_usd=0.0).action == "refuse"
    clock.advance(minutes=1, seconds=1)
    assert ledger.preflight("opencode-go", "glm-5.3", est_equivalent_usd=0.0).action == "allow"


def test_event_exactly_window_old_has_aged_out() -> None:
    clock = FakeClock()
    ledger = _ledger(clock)
    ledger.record("opencode-go", "glm-5.3", equivalent_usd=2.0)
    clock.advance(hours=5)
    assert ledger.preflight("opencode-go", "glm-5.3", est_equivalent_usd=0.0).action == "allow"


def test_week_window_boundary() -> None:
    clock = FakeClock()
    ledger = _ledger(clock)
    for _ in range(3):  # 3 × 1.8 = 5.4 > week 5.0, spaced beyond the 5 h window
        ledger.record("opencode-go", "glm-5.3", equivalent_usd=1.8)
        clock.advance(hours=6)
    v = ledger.preflight("opencode-go", "glm-5.3", est_equivalent_usd=0.0)
    assert (v.action, v.exhausted_window) == ("refuse", "week")
    clock.now = T0 + timedelta(days=7, seconds=1)  # first event ages out → 3.6 < 5.0
    assert ledger.preflight("opencode-go", "glm-5.3", est_equivalent_usd=0.0).action == "allow"


def test_month_window_is_rolling_30_days() -> None:
    clock = FakeClock()
    ledger = _ledger(clock)
    for _ in range(6):  # 6 × 1.7 = 10.2 > month 10, spaced beyond the week window
        ledger.record("opencode-go", "glm-5.3", equivalent_usd=1.7)
        clock.advance(days=5)
    clock.now = T0 + timedelta(days=29)
    v = ledger.preflight("opencode-go", "glm-5.3", est_equivalent_usd=0.0)
    assert (v.action, v.exhausted_window) == ("refuse", "month")
    clock.now = T0 + timedelta(days=30, seconds=1)
    assert ledger.preflight("opencode-go", "glm-5.3", est_equivalent_usd=0.0).action == "allow"


def test_downgrade_action_returns_configured_model() -> None:
    ledger = _ledger(
        opencode_go_quota_action="downgrade", opencode_go_downgrade_model="gw:opencode-go/glm-5.2"
    )
    ledger.record("opencode-go", "glm-5.3", equivalent_usd=2.0)
    v = ledger.preflight("opencode-go", "glm-5.3", est_equivalent_usd=0.0)
    assert v == QuotaVerdict(
        allowed=False,
        action="downgrade",
        exhausted_window="5h",
        downgrade_model="gw:opencode-go/glm-5.2",
    )


def test_free_daily_requests_refuse_after_limit_and_reset_next_utc_day() -> None:
    clock = FakeClock(datetime(2026, 10, 5, 23, 0, tzinfo=UTC))
    ledger = _ledger(clock)
    model = "meta-llama/llama-4:free"
    for _ in range(OPENROUTER_FREE_DAILY_LIMIT - 1):
        ledger.record("openrouter", model, equivalent_usd=0.0)
    assert ledger.preflight("openrouter", model, est_equivalent_usd=0.0).action == "allow"
    ledger.record("openrouter", model, equivalent_usd=0.0)
    v = ledger.preflight("openrouter", model, est_equivalent_usd=0.0)
    assert (v.action, v.exhausted_window) == ("refuse", "day")
    clock.now = datetime(2026, 10, 6, 0, 0, 1, tzinfo=UTC)  # calendar rollover, not 24 h
    assert ledger.preflight("openrouter", model, est_equivalent_usd=0.0).action == "allow"


def test_free_limit_shared_across_free_models_and_always_refuses() -> None:
    ledger = _ledger(
        opencode_go_quota_action="downgrade", opencode_go_downgrade_model="gw:opencode-go/glm-5.2"
    )
    for i in range(OPENROUTER_FREE_DAILY_LIMIT):
        ledger.record("openrouter", f"m{i % 3}:free", equivalent_usd=0.0)
    ledger.record("openrouter", "anthropic/claude-sonnet-4.5", equivalent_usd=3.0)
    v = ledger.preflight("openrouter", "other:free", est_equivalent_usd=0.0)
    assert v.action == "refuse"
    assert v.downgrade_model is None
    assert ledger.preflight(
        "openrouter", "anthropic/claude-sonnet-4.5", est_equivalent_usd=9.0
    ).allowed


def test_no_windows_always_allows() -> None:
    ledger = _ledger(opencode_go_monthly_cap_usd=0.0)
    ledger.record("opencode-go", "glm-5.3", equivalent_usd=1_000.0)
    assert ledger.preflight("opencode-go", "glm-5.3", est_equivalent_usd=1_000.0).allowed


# ── isolation ────────────────────────────────────────────────────────────────


def test_orgs_never_share_usage() -> None:
    store = InMemoryQuotaStore()
    clock = FakeClock()
    settings = _go_settings()
    a = QuotaLedger(settings=settings, org_id="a", clock=clock, store=store)
    b = QuotaLedger(settings=settings, org_id="b", clock=clock, store=store)
    a.record("opencode-go", "glm-5.3", equivalent_usd=2.0)
    assert a.preflight("opencode-go", "glm-5.3", est_equivalent_usd=0.0).action == "refuse"
    assert b.preflight("opencode-go", "glm-5.3", est_equivalent_usd=0.0).action == "allow"


def test_gateways_never_share_usage() -> None:
    ledger = _ledger()
    for _ in range(OPENROUTER_FREE_DAILY_LIMIT):
        ledger.record("openrouter", "x:free", equivalent_usd=5.0)
    assert ledger.preflight("opencode-go", "glm-5.3", est_equivalent_usd=0.0).action == "allow"


def test_store_prunes_events_older_than_longest_window() -> None:
    store = InMemoryQuotaStore()
    clock = FakeClock()
    ledger = QuotaLedger(settings=_go_settings(), org_id="o", clock=clock, store=store)
    ledger.record("opencode-go", "glm-5.3", equivalent_usd=1.0)
    clock.advance(days=31)
    ledger.record("opencode-go", "glm-5.3", equivalent_usd=1.0)
    assert len(store.events(("o", "opencode-go"))) == 1


def test_record_skipped_when_no_windows() -> None:
    store = InMemoryQuotaStore()
    ledger = QuotaLedger(settings=_go_settings(), org_id="o", clock=FakeClock(), store=store)
    ledger.record("openrouter", "anthropic/claude-sonnet-4.5", equivalent_usd=1.0)
    assert store.events(("o", "openrouter")) == []


# ── snapshot ─────────────────────────────────────────────────────────────────


def test_snapshot_reports_ledger_windows_with_resets_at() -> None:
    clock = FakeClock()
    ledger = _ledger(clock)
    ledger.record("opencode-go", "glm-5.3", equivalent_usd=0.5)
    clock.advance(hours=1)
    ledger.record("opencode-go", "glm-5.3", equivalent_usd=0.25)
    snaps = {s.window: s for s in ledger.snapshot("opencode-go")}
    assert set(snaps) == {"5h", "week", "month"}
    five = snaps["5h"]
    assert five.gateway == "opencode-go"
    assert five.source == "ledger"
    assert five.unit == "usd"
    assert five.limit == pytest.approx(2.0)
    assert five.used == pytest.approx(0.75)
    assert five.remaining == pytest.approx(1.25)
    assert five.resets_at == T0 + timedelta(hours=5)
    assert snaps["week"].resets_at == T0 + timedelta(days=7)
    assert snaps["month"].resets_at == T0 + timedelta(days=30)


def test_snapshot_empty_window_has_no_reset() -> None:
    (snap5, *_rest) = _ledger().snapshot("opencode-go")
    assert snap5.used == 0.0
    assert snap5.resets_at is None


def test_snapshot_free_day_resets_at_next_utc_midnight() -> None:
    ledger = _ledger()
    ledger.record("openrouter", "x:free", equivalent_usd=0.0)
    (snap,) = ledger.snapshot("openrouter")
    assert (snap.window, snap.unit, snap.used, snap.remaining) == ("day", "requests", 1, 49)
    assert snap.resets_at == datetime(2026, 10, 6, tzinfo=UTC)


def test_snapshot_unwindowed_gateway_is_empty() -> None:
    assert _ledger().snapshot("opencode") == []


# ── fail-open ────────────────────────────────────────────────────────────────


class BrokenStore:
    def add(self, key: Any, event: Any) -> None:
        raise RuntimeError("disk full")

    def events(self, key: Any) -> list[Any]:
        raise RuntimeError("disk full")

    def prune(self, key: Any, before: Any) -> None:
        raise RuntimeError("disk full")


def test_ledger_failures_are_fail_open(otel: Any) -> None:
    ledger = QuotaLedger(
        settings=_go_settings(), org_id="o", clock=FakeClock(), store=BrokenStore()
    )
    ledger.record("opencode-go", "glm-5.3", equivalent_usd=1.0)  # never raises
    v = ledger.preflight("opencode-go", "glm-5.3", est_equivalent_usd=99.0)
    assert v.allowed and v.action == "allow"
    assert ledger.snapshot("opencode-go") == []
    kinds = [
        c
        for c in otel.counts["gateway_errors"]
        if c == {"gateway": "opencode-go", "kind": "ledger"}
    ]
    assert len(kinds) == 3


def test_default_clock_and_settings_work() -> None:
    ledger = QuotaLedger()
    assert ledger.preflight("opencode-go", "glm-5.3", est_equivalent_usd=0.0).allowed
