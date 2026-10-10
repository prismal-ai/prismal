"""Quota poller → gauge (Phase GW — SPEC-GW-QUOTA-001, GW5-05)."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import SecretStr

from prismal.providers.gateways.base import GatewayProfile, QuotaSnapshot
from prismal.providers.gateways.openai_compatible import OpenAICompatibleGateway
from prismal.providers.gateways.poller import GatewayQuotaPoller
from prismal.providers.gateways.router import GatewayRouter

from .conftest import gw_settings


class _Q(OpenAICompatibleGateway):
    def __init__(self, remaining: float | None) -> None:
        super().__init__(
            GatewayProfile(name="q", base_url="https://q/v1", auth_ref="k"),
            secret_resolver=lambda ref: SecretStr("s"),
        )
        self.remaining = remaining
        self.polls = 0

    async def quota(self) -> QuotaSnapshot | None:
        self.polls += 1
        return QuotaSnapshot(
            gateway="q",
            window="credit",
            unit="usd",
            limit=10.0,
            used=1.0,
            remaining=self.remaining,
            resets_at=None,
            source="api",
        )


async def test_poll_once_sets_gauge(otel: Any) -> None:
    router = GatewayRouter({"q": _Q(9.0)}, settings=gw_settings())
    snaps = await GatewayQuotaPoller(router).poll_once()
    assert [s.remaining for s in snaps] == [9.0]
    assert otel.counts["gateway_quota_remaining"] == [
        {"value": 9.0, "gateway": "q", "window": "credit"}
    ]


async def test_unknown_remaining_not_gauged(otel: Any) -> None:
    router = GatewayRouter({"q": _Q(None)}, settings=gw_settings())
    await GatewayQuotaPoller(router).poll_once()
    assert "gateway_quota_remaining" not in otel.counts


async def test_start_polls_periodically_and_aclose_stops() -> None:
    gw = _Q(9.0)
    poller = GatewayQuotaPoller(GatewayRouter({"q": gw}, settings=gw_settings()), interval_s=0.01)
    poller.start()
    poller.start()  # idempotent
    await asyncio.sleep(0.05)
    await poller.aclose()
    polls = gw.polls
    assert polls >= 2
    await asyncio.sleep(0.03)
    assert gw.polls == polls


async def test_poll_failure_keeps_loop_alive(otel: Any) -> None:
    class _Broken(GatewayRouter):
        async def quotas(self) -> list[QuotaSnapshot]:
            raise RuntimeError("down")

    poller = GatewayQuotaPoller(_Broken({}, settings=gw_settings()), interval_s=0.01)
    assert await poller.poll_once() == []
    assert otel.counts["gateway_errors"] == [{"gateway": "*", "kind": "quota_poll"}]
