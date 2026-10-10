"""CostReconciler (Phase GW — SPEC-GW-REC-001, GW5-04)."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import SecretStr

from prismal.providers.gateways.base import (
    GatewayCallMeta,
    GatewayCapabilities,
    GatewayProfile,
    GenerationStats,
)
from prismal.providers.gateways.openai_compatible import OpenAICompatibleGateway
from prismal.providers.gateways.reconcile import CostReconciler
from prismal.providers.gateways.router import GatewayRouter

from .conftest import gw_settings


class _GenGateway(OpenAICompatibleGateway):
    def __init__(self, cost: float | None, *, fail: bool = False, has_api: bool = True) -> None:
        super().__init__(
            GatewayProfile(
                name="openrouter",
                base_url="https://o/v1",
                auth_ref="k",
                capabilities=GatewayCapabilities(has_generation_api=has_api),
            ),
            secret_resolver=lambda ref: SecretStr("s"),
        )
        self.cost = cost
        self.fail = fail
        self.asked: list[str] = []

    async def fetch_generation(self, generation_id: str) -> GenerationStats | None:
        self.asked.append(generation_id)
        if self.fail:
            raise RuntimeError("down")
        if self.cost is None:
            return None
        return GenerationStats(
            generation_id=generation_id,
            total_cost_usd=self.cost,
            served_model="m",
            provider_name=None,
            prompt_tokens=1,
            completion_tokens=1,
        )


def _meta(gen_id: str | None = "gen-1", cost: float | None = 0.10) -> GatewayCallMeta:
    return GatewayCallMeta(
        gateway="openrouter",
        requested_model="m",
        served_model="m",
        generation_id=gen_id,
        reported_cost_usd=cost,
        cost_mode="metered",
    )


def _reconciler(gw: _GenGateway, **kw: Any) -> CostReconciler:
    s = gw_settings(gateway_reconcile_enabled=True, gateway_reconcile_delay_s=0.0)
    return CostReconciler(GatewayRouter({"openrouter": gw}, settings=s), settings=s, **kw)


async def test_records_drift(otel: Any) -> None:
    gw = _GenGateway(cost=0.13)
    rec = _reconciler(gw)
    rec.schedule(_meta())
    await rec.drain()
    assert gw.asked == ["gen-1"]
    drift = otel.counts["gateway_cost_drift_usd"]
    assert len(drift) == 1 and abs(drift[0]["value"] - 0.03) < 1e-9
    assert drift[0]["gateway"] == "openrouter"


async def test_disabled_does_nothing() -> None:
    gw = _GenGateway(cost=0.13)
    s = gw_settings(gateway_reconcile_enabled=False)
    rec = CostReconciler(GatewayRouter({"openrouter": gw}, settings=s), settings=s)
    rec.schedule(_meta())
    await rec.drain()
    assert gw.asked == []


async def test_skips_without_generation_id_or_api() -> None:
    gw = _GenGateway(cost=0.13, has_api=False)
    rec = _reconciler(gw)
    rec.schedule(_meta())
    gw2 = _GenGateway(cost=0.13)
    rec2 = _reconciler(gw2)
    rec2.schedule(_meta(gen_id=None))
    rec2.schedule(_meta(cost=None))
    await rec.drain()
    await rec2.drain()
    assert gw.asked == [] and gw2.asked == []


async def test_fetch_failure_is_silent(otel: Any) -> None:
    gw = _GenGateway(cost=0.13, fail=True)
    rec = _reconciler(gw)
    rec.schedule(_meta())
    await rec.drain()
    assert "gateway_cost_drift_usd" not in otel.counts
    assert otel.counts["gateway_errors"] == [{"gateway": "openrouter", "kind": "reconcile"}]


async def test_queue_full_drops(otel: Any) -> None:
    gw = _GenGateway(cost=0.13)
    rec = _reconciler(gw, max_pending=2)
    for _ in range(5):
        rec.schedule(_meta())
    assert rec.pending == 2
    assert (
        otel.counts["gateway_errors"].count({"gateway": "openrouter", "kind": "reconcile_dropped"})
        == 3
    )
    await rec.drain()


async def test_schedule_without_running_loop_is_noop() -> None:
    gw = _GenGateway(cost=0.13)
    rec = _reconciler(gw)

    def outside() -> None:
        rec.schedule(_meta())

    await asyncio.to_thread(outside)
    await rec.drain()
    assert gw.asked == []


async def test_drain_times_out_without_raising() -> None:
    class _Slow(_GenGateway):
        async def fetch_generation(self, generation_id: str) -> GenerationStats | None:
            await asyncio.sleep(10)
            return None

    rec = _reconciler(_Slow(cost=0.1))
    rec.schedule(_meta())
    await rec.drain(timeout_s=0.05)
    assert rec.pending == 0
