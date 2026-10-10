"""``RuntimeContext.quota_windows`` (HRS — SPEC-HRS-QTA-001)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from prismal.composition import build_test_runtime
from prismal.composition.host_surface import QuotaWindowView
from prismal.core.config import Settings
from prismal.providers.gateways.base import QuotaSnapshot
from prismal.providers.gateways.router import GatewayRouter

pytestmark = pytest.mark.unit

RESET = datetime(2026, 10, 4, 15, 10, tzinfo=UTC)


class _Gateway:
    def __init__(self, snap: QuotaSnapshot | None = None, *, fail: bool = False) -> None:
        self._snap = snap
        self._fail = fail

    async def quota(self) -> QuotaSnapshot | None:
        if self._fail:
            raise RuntimeError("upstream down")
        return self._snap


class _MultiGateway(_Gateway):
    def __init__(self, snaps: list[QuotaSnapshot]) -> None:
        super().__init__(snaps[0])
        self._snaps = snaps

    async def quotas(self) -> list[QuotaSnapshot]:
        return list(self._snaps)


class _Ledger:
    def __init__(self, by_gateway: dict[str, list[QuotaSnapshot]]) -> None:
        self._by = by_gateway

    def snapshot(self, gateway: str) -> list[QuotaSnapshot]:
        return list(self._by.get(gateway, []))


def _snap(gateway: str, window: Any, *, source: Any = "api", **kw: Any) -> QuotaSnapshot:
    base: dict[str, Any] = {
        "unit": "usd",
        "limit": 50.0,
        "used": 8.8,
        "remaining": 41.2,
        "resets_at": None,
    }
    base.update(kw)
    return QuotaSnapshot(gateway=gateway, window=window, source=source, **base)


def _ctx(gateways: dict[str, Any], ledger: Any | None = None) -> Any:
    ctx = build_test_runtime(settings=Settings())
    ctx.gateways = GatewayRouter(gateways, settings=Settings(), ledger=ledger)
    return ctx


async def test_no_gateways_returns_empty() -> None:
    assert await build_test_runtime(settings=Settings()).quota_windows() == []


async def test_openrouter_credit_and_windows_from_api() -> None:
    gw = _MultiGateway(
        [
            _snap("openrouter", "credit"),
            _snap("openrouter", "day", limit=None, remaining=None, used=1.0),
            _snap("openrouter", "week", limit=None, remaining=None, used=2.0),
            _snap("openrouter", "month", limit=None, remaining=None, used=3.0),
        ]
    )
    views = await _ctx({"openrouter": gw}).quota_windows()
    assert [v.window for v in views] == ["credit", "daily", "weekly", "monthly"]
    credit = views[0]
    assert isinstance(credit, QuotaWindowView)
    assert (credit.gateway, credit.unit, credit.source) == ("openrouter", "usd", "api")
    assert (credit.limit, credit.used, credit.remaining) == (50.0, 8.8, 41.2)
    assert credit.as_of.tzinfo is not None


async def test_opencode_go_ledger_windows_are_usd_equivalent_and_deduped() -> None:
    five_h = _snap(
        "opencode", "5h", source="ledger", limit=2.0, used=0.6, remaining=1.4, resets_at=RESET
    )
    week = _snap("opencode", "week", source="ledger", limit=10.0, used=1.0, remaining=9.0)
    gw = _Gateway(five_h)  # quota() returns the tightest ledger window
    views = await _ctx(
        {"opencode": gw}, ledger=_Ledger({"opencode": [five_h, week]})
    ).quota_windows()
    assert [(v.window, v.source) for v in views] == [("5h", "ledger"), ("weekly", "ledger")]
    assert views[0].unit == "usd_equivalent"
    assert views[0].resets_at == RESET


async def test_requests_unit_passes_through() -> None:
    snap = _snap("opencode", "day", source="ledger", unit="requests", limit=100.0, used=3.0)
    views = await _ctx(
        {"opencode": _Gateway()}, ledger=_Ledger({"opencode": [snap]})
    ).quota_windows()
    assert (views[0].window, views[0].unit) == ("daily", "requests")


async def test_failing_gateway_is_omitted_and_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    counter = MagicMock()
    monkeypatch.setattr("prismal.monitoring.otel.OTelManager.increment_counter", counter)
    good = _Gateway(_snap("openrouter", "credit"))
    views = await _ctx({"openrouter": good, "broken": _Gateway(fail=True)}).quota_windows()
    assert [v.gateway for v in views] == ["openrouter"]
    counter.assert_any_call("host_surface_errors", attributes={"method": "quota_windows"})


async def test_gateway_without_quota_yields_nothing() -> None:
    assert await _ctx({"generic": _Gateway(None)}).quota_windows() == []
