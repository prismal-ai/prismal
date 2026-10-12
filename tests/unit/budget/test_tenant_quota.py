"""Shared tenant quota counters (DELTA-2026-10-shared-tenant-quota, TQ-001…TQ-009)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import HumanMessage

from prismal.budget.guard import BudgetGuard
from prismal.budget.meter import CostMeter
from prismal.budget.resolve import clear_budget_run, get_budget_guard, seed_budget_run
from prismal.budget.tenant_quota import (
    InMemoryTenantQuotaStore,
    SqliteTenantQuotaStore,
    TenantCostMeter,
    TenantQuotaStorePort,
    set_tenant_quota_store,
    window_key,
)
from prismal.budget.types import Budget, Usage
from prismal.core.config import Settings
from prismal.langgraph import create_initial_state
from prismal.monitoring.host_stats import use_org

NOW = datetime(2026, 10, 12, 15, 30, tzinfo=UTC)


def _call(tokens: int = 100, cost: float = 0.01) -> Usage:
    return Usage(
        prompt_tokens=tokens, completion_tokens=0, cost_usd=cost, calls=1, wall_clock_s=1.0
    )


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> TenantQuotaStorePort:
    if request.param == "memory":
        return InMemoryTenantQuotaStore()
    return SqliteTenantQuotaStore(tmp_path / "quota.db")


# ── store ───────────────────────────────────────────────────────────────────


def test_window_key_is_utc_calendar() -> None:
    assert window_key(NOW, "month") == "2026-10"
    assert window_key(NOW, "day") == "2026-10-12"


def test_store_accumulates_and_returns_the_total(store: TenantQuotaStorePort) -> None:
    assert store.add("acme", "2026-10", _call(100, 0.01)).total_tokens == 100
    total = store.add("acme", "2026-10", _call(50, 0.02))

    assert (total.total_tokens, total.calls) == (150, 2)
    assert total.cost_usd == pytest.approx(0.03)
    assert store.get("acme", "2026-10") == total


def test_store_isolates_tenants_and_windows(store: TenantQuotaStorePort) -> None:
    store.add("acme", "2026-10", _call(100))
    store.add("globex", "2026-10", _call(7))
    store.add("acme", "2026-11", _call(3))

    assert store.get("acme", "2026-10").total_tokens == 100
    assert store.get("globex", "2026-10").total_tokens == 7
    assert store.get("acme", "2026-11").total_tokens == 3
    assert store.get("initech", "2026-10") == Usage()


def test_sqlite_totals_are_shared_by_every_store_on_the_same_file(tmp_path: Path) -> None:
    path = tmp_path / "quota.db"
    worker_a, worker_b = SqliteTenantQuotaStore(path), SqliteTenantQuotaStore(path)

    worker_a.add("acme", "2026-10", _call(100))
    total = worker_b.add("acme", "2026-10", _call(20))

    assert total.total_tokens == 120


def test_sqlite_increments_are_atomic_under_concurrency(tmp_path: Path) -> None:
    path = tmp_path / "quota.db"

    def work() -> None:
        store = SqliteTenantQuotaStore(path)
        for _ in range(25):
            store.add("acme", "2026-10", _call(1, 0.0))

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    total = SqliteTenantQuotaStore(path).get("acme", "2026-10")
    assert (total.total_tokens, total.calls) == (200, 200)


# ── meter + guard ───────────────────────────────────────────────────────────


def _meter(store: TenantQuotaStorePort, org: str = "acme") -> TenantCostMeter:
    return TenantCostMeter(store=store, org_id=org, window="2026-10")


def test_runs_of_one_tenant_share_the_budget() -> None:
    store = InMemoryTenantQuotaStore()
    budget = Budget(max_tokens=250)
    first = BudgetGuard(budget, _meter(store))
    first.meter.record(_call(200))
    assert first.check().within is False  # soft (200/250 >= 0.8)

    second = BudgetGuard(budget, _meter(store))  # a new run sees the tenant's spend
    assert second.meter.usage.total_tokens == 200
    second.meter.record(_call(100))

    assert second.check().hard_exceeded is True


def test_other_tenants_are_unaffected() -> None:
    store = InMemoryTenantQuotaStore()
    _meter(store, "acme").record(_call(1000))

    assert BudgetGuard(Budget(max_tokens=250), _meter(store, "globex")).check().within is True


def test_a_failing_store_falls_back_to_local_accumulation() -> None:
    class Broken(InMemoryTenantQuotaStore):
        def add(self, org_id: str, window: str, usage: Usage) -> Usage:
            raise OSError("disk full")

    meter = _meter(Broken())
    meter.record(_call(100))
    meter.record(_call(100))

    assert meter.usage.total_tokens == 200


# ── seeding (TQ-001, TQ-006, TQ-009) ────────────────────────────────────────


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "budget_enabled": True,
        "budget_scope": "tenant",
        "budget_max_tokens": 250,
    }
    base.update(kw)
    return Settings(**base)


@pytest.fixture
def shared_store() -> Any:
    store = InMemoryTenantQuotaStore()
    set_tenant_quota_store(store)
    yield store
    set_tenant_quota_store(None)


def _state(session: str) -> Any:
    state = create_initial_state(session_id=session)
    state["messages"] = [HumanMessage(content="hi")]
    return state


def test_tenant_scope_seeds_a_shared_meter_for_the_bound_org(shared_store: Any) -> None:
    shared_store.add("acme", window_key(datetime.now(UTC), "month"), _call(240))
    state = _state("s-tq-1")
    try:
        with use_org("acme"):
            seed_budget_run(state, _settings())
        guard = get_budget_guard(state)
        assert isinstance(guard, BudgetGuard)
        assert isinstance(guard.meter, TenantCostMeter)
        assert guard.meter.usage.total_tokens == 240
    finally:
        clear_budget_run(state)


def test_tenant_scope_without_an_org_falls_back_to_a_run_meter(shared_store: Any) -> None:
    state = _state("s-tq-2")
    try:
        seed_budget_run(state, _settings())
        guard = get_budget_guard(state)
        assert guard is not None
        assert type(guard.meter) is CostMeter
    finally:
        clear_budget_run(state)


def test_other_scopes_never_use_the_tenant_store(shared_store: Any) -> None:
    state = _state("s-tq-3")
    try:
        with use_org("acme"):
            seed_budget_run(state, _settings(budget_scope="turn"))
        guard = get_budget_guard(state)
        assert guard is not None
        assert type(guard.meter) is CostMeter
    finally:
        clear_budget_run(state)


def test_unknown_window_fails_settings_load() -> None:
    with pytest.raises(ValueError, match="BUDGET_TENANT_WINDOW"):
        _settings(budget_tenant_window="week")
