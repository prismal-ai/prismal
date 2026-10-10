"""CostMeter gateway metering (Phase GW — SPEC-GW-COST-001, GW5-02)."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage

from prismal.budget.meter import CostMeter
from prismal.budget.types import Usage
from prismal.core.config import Settings
from prismal.providers.gateways.base import GatewayCallMeta


class _Otel:
    def __init__(self) -> None:
        self.counters: list[tuple[str, int, dict[str, Any]]] = []

    def increment_counter(self, name: str, value: int = 1, attributes: Any = None) -> None:
        self.counters.append((name, value, attributes or {}))

    def record_histogram(self, name: str, value: float, attributes: Any = None) -> None:
        pass


@pytest.fixture
def otel(monkeypatch: pytest.MonkeyPatch) -> _Otel:
    fake = _Otel()
    monkeypatch.setattr("prismal.monitoring.otel.OTelManager", lambda: fake)
    return fake


_TABLE = Settings(budget_pricing={"openai/glm-5": {"input": 1.0, "output": 2.0}})


def _msg(*, cost: float | None, mode: str = "metered", served: str | None = "a/b") -> AIMessage:
    meta = GatewayCallMeta(
        gateway="openrouter" if mode == "metered" else "opencode-go",
        requested_model="a/b",
        served_model=served,
        generation_id="gen-1",
        reported_cost_usd=cost,
        cost_mode=mode,  # type: ignore[arg-type]
    )
    return AIMessage(
        content="x",
        usage_metadata={"input_tokens": 1000, "output_tokens": 1000, "total_tokens": 2000},
        response_metadata={"prismal_gateway": meta.to_dict()},
    )


def test_openrouter_call_meters_gateway_source(otel: _Otel) -> None:
    usage = CostMeter(settings=_TABLE).record_response(_msg(cost=0.42), "openrouter/a/b")
    assert usage.cost_usd == 0.42
    assert usage.cost_source == "gateway"
    assert usage.estimated is False
    assert usage.gateway == "openrouter"
    assert usage.served_model == "a/b"
    attrs = next(a for name, _, a in otel.counters if name == "budget_tokens")
    assert attrs["gateway"] == "openrouter" and attrs["served_model"] == "a/b"


def test_go_call_meters_zero_cost_with_equivalent() -> None:
    usage = CostMeter(settings=_TABLE).record_response(
        _msg(cost=None, mode="subscription"), "openai/glm-5"
    )
    assert usage.cost_usd == 0.0
    assert usage.cost_source == "subscription"
    assert usage.equivalent_usd == pytest.approx(3.0)


def test_explicit_reported_cost_beats_meta() -> None:
    usage = CostMeter(settings=_TABLE).record_response(
        _msg(cost=0.42), "openrouter/a/b", reported_cost_usd=0.1
    )
    assert usage.cost_usd == 0.1


def test_non_gateway_message_unchanged(otel: _Otel) -> None:
    plain = AIMessage(
        content="x",
        usage_metadata={"input_tokens": 1000, "output_tokens": 1000, "total_tokens": 2000},
    )
    usage = CostMeter(settings=_TABLE).record_response(plain, "openai/glm-5")
    assert usage.cost_source == "" and usage.gateway == "" and usage.equivalent_usd == 0.0
    attrs = next(a for name, _, a in otel.counters if name == "budget_tokens")
    assert set(attrs) == {"agent", "pattern", "model", "tenant"}


def test_usage_sums_equivalent_and_keeps_last_gateway() -> None:
    a = Usage(equivalent_usd=1.0, gateway="g1", served_model="m1")
    b = Usage(equivalent_usd=2.0)
    total = a + b
    assert total.equivalent_usd == 3.0
    assert (total.gateway, total.served_model) == ("g1", "m1")
    assert (b + Usage(gateway="g2")).gateway == "g2"


# ── GW5-03 / GW5-05: ledger record, reconciler, quota gauge, degradation ─────


class _Ledger:
    def __init__(self) -> None:
        self.recorded: list[tuple[str, str, float]] = []

    def record(self, gateway: str, model: str, *, equivalent_usd: float, requests: int = 1) -> None:
        self.recorded.append((gateway, model, equivalent_usd))

    def snapshot(self, gateway: str) -> list[Any]:
        from prismal.providers.gateways.base import QuotaSnapshot

        return [
            QuotaSnapshot(
                gateway=gateway,
                window="5h",
                unit="usd",
                limit=2.0,
                used=0.5,
                remaining=1.5,
                resets_at=None,
                source="ledger",
            )
        ]


class _Reconciler:
    def __init__(self) -> None:
        self.scheduled: list[GatewayCallMeta] = []

    def schedule(self, meta: GatewayCallMeta) -> None:
        self.scheduled.append(meta)


class _Router:
    def __init__(self) -> None:
        self.ledger = _Ledger()
        self.reconciler = _Reconciler()


class _GaugeOtel(_Otel):
    def __init__(self) -> None:
        super().__init__()
        self.gauges: list[tuple[str, float, dict[str, Any]]] = []

    def set_gauge(self, name: str, value: float, attributes: Any = None) -> None:
        self.gauges.append((name, value, attributes or {}))


def test_subscription_call_recorded_in_ledger_and_gauge(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _GaugeOtel()
    monkeypatch.setattr("prismal.monitoring.otel.OTelManager", lambda: fake)
    router = _Router()
    meter = CostMeter(settings=_TABLE, gateway_router=router)  # type: ignore[arg-type]
    meter.record_response(_msg(cost=None, mode="subscription"), "openai/glm-5")
    assert router.ledger.recorded == [("opencode-go", "a/b", pytest.approx(3.0))]
    assert (
        "gateway_quota_remaining",
        1.5,
        {"gateway": "opencode-go", "window": "5h"},
    ) in fake.gauges
    assert [m.generation_id for m in router.reconciler.scheduled] == ["gen-1"]


def test_router_found_via_contextvar(monkeypatch: pytest.MonkeyPatch) -> None:
    router = _Router()
    monkeypatch.setattr("prismal.providers.gateways.router.get_gateway_router", lambda: router)
    CostMeter(settings=_TABLE).record_response(_msg(cost=0.2), "openrouter/a/b")
    assert router.ledger.recorded and router.reconciler.scheduled


def test_ledger_failure_never_breaks_metering() -> None:
    class _Boom(_Router):
        def __init__(self) -> None:
            super().__init__()
            self.ledger.record = None  # type: ignore[assignment,method-assign]

    usage = CostMeter(settings=_TABLE, gateway_router=_Boom()).record_response(  # type: ignore[arg-type]
        _msg(cost=0.2), "openrouter/a/b"
    )
    assert usage.cost_usd == 0.2


def test_quota_downgrade_becomes_budget_degradation() -> None:
    from prismal.budget.guard import BudgetGuard
    from prismal.budget.types import Budget

    meta = GatewayCallMeta(
        gateway="opencode-go",
        requested_model="glm-5",
        served_model="glm-5-flash",
        generation_id=None,
        reported_cost_usd=None,
        cost_mode="subscription",
        quota_downgrade="5h",
    )
    msg = AIMessage(content="x", response_metadata={"prismal_gateway": meta.to_dict()})
    meter = CostMeter(settings=_TABLE)
    guard = BudgetGuard(Budget(), meter)
    assert guard.degradation().reduce is False
    meter.record_response(msg, "openai/glm-5")
    advice = guard.degradation()
    assert advice.reduce is True and advice.reason == "gateway_quota:5h"
