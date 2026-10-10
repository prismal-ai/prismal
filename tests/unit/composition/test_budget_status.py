"""``RuntimeContext.budget_status`` (HRS — SPEC-HRS-BUD-001)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from prismal.budget.resolve import _RUN_ENGINES, seed_budget_run
from prismal.budget.types import Usage
from prismal.composition import build_test_runtime
from prismal.composition.host_surface import BudgetAmounts, BudgetStatusView
from prismal.core.config import Settings

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    _RUN_ENGINES.clear()
    yield
    _RUN_ENGINES.clear()


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "budget_enabled": True,
        "budget_scope": "session",
        "budget_max_tokens": 1000,
        "budget_max_cost_usd": 2.0,
        "budget_soft_ratio": 0.8,
    }
    base.update(kw)
    return Settings(**base)


def _seed(settings: Settings, thread: str, usage: Usage, org_id: str | None = None) -> None:
    state: dict[str, Any] = {"session_id": thread, "messages": []}
    seed_budget_run(state, settings, org_id=org_id)  # type: ignore[arg-type]
    _RUN_ENGINES[thread]["meter"].record(usage)


def test_disabled_reports_disabled_with_null_limits() -> None:
    view = build_test_runtime(settings=Settings()).budget_status(thread_id="t-1")
    assert view == BudgetStatusView(
        enabled=False,
        scope="turn",
        status="disabled",
        breached_dimension=None,
        soft_ratio=None,
        hard_cap=None,
        limits=None,
        consumed=None,
        remaining=None,
    )


def test_session_scope_without_thread_is_unknown() -> None:
    view = build_test_runtime(settings=_settings()).budget_status()
    assert (view.enabled, view.scope, view.status) == (True, "session", "unknown")
    assert view.consumed is None and view.remaining is None
    assert view.limits == BudgetAmounts(tokens=1000, cost_usd=2.0, calls=0, wall_clock_s=0.0)
    assert (view.soft_ratio, view.hard_cap) == (0.8, True)


def test_unseeded_thread_is_unknown() -> None:
    view = build_test_runtime(settings=_settings()).budget_status(thread_id="nope")
    assert view.status == "unknown" and view.consumed is None


def test_within_budget_reports_consumed_and_remaining() -> None:
    settings = _settings()
    _seed(settings, "t-1", Usage(prompt_tokens=100, completion_tokens=50, cost_usd=0.5, calls=2))
    view = build_test_runtime(settings=settings).budget_status(thread_id="t-1")
    assert view.status == "within" and view.breached_dimension is None
    assert view.consumed == BudgetAmounts(tokens=150, cost_usd=0.5, calls=2, wall_clock_s=0.0)
    # 0 limit (calls / wall clock) ⇒ unlimited ⇒ remaining None
    assert view.remaining == BudgetAmounts(tokens=850, cost_usd=1.5, calls=None, wall_clock_s=None)


def test_soft_exceeded_maps_dimension_to_wire_name() -> None:
    settings = _settings()
    _seed(settings, "t-1", Usage(cost_usd=1.8))
    view = build_test_runtime(settings=settings).budget_status(thread_id="t-1")
    assert (view.status, view.breached_dimension) == ("soft_exceeded", "cost_usd")


def test_hard_exceeded_clamps_remaining_at_zero() -> None:
    settings = _settings()
    _seed(settings, "t-1", Usage(prompt_tokens=1500))
    view = build_test_runtime(settings=settings).budget_status(thread_id="t-1")
    assert (view.status, view.breached_dimension) == ("hard_exceeded", "tokens")
    assert view.remaining is not None and view.remaining.tokens == 0


def test_turn_scope_uses_per_run_meter() -> None:
    settings = _settings(budget_scope="turn", budget_max_calls=4)
    _seed(settings, "t-9", Usage(calls=4))
    view = build_test_runtime(settings=settings).budget_status(thread_id="t-9")
    assert (view.scope, view.status, view.breached_dimension) == ("turn", "hard_exceeded", "calls")


def test_wall_clock_dimension_name() -> None:
    settings = _settings(budget_max_wall_clock_s=10.0)
    _seed(settings, "t-1", Usage(wall_clock_s=10.0))
    view = build_test_runtime(settings=settings).budget_status(thread_id="t-1")
    assert view.breached_dimension == "wall_clock_s"


def test_tenant_scope_without_tenant_meter_is_unknown() -> None:
    settings = _settings(budget_scope="tenant")
    _seed(settings, "t-1", Usage(prompt_tokens=10))
    view = build_test_runtime(settings=settings, org_id="acme").budget_status(thread_id="t-1")
    assert (view.scope, view.status, view.consumed) == ("tenant", "unknown", None)


def test_other_tenants_thread_is_not_visible() -> None:
    settings = _settings()
    _seed(settings, "t-1", Usage(prompt_tokens=10), org_id="beta")
    acme = build_test_runtime(settings=settings, org_id="acme").budget_status(thread_id="t-1")
    assert acme.status == "unknown"
    beta = build_test_runtime(settings=settings, org_id="beta").budget_status(thread_id="t-1")
    assert beta.status == "within"


def test_disabled_still_reports_consumed_of_an_existing_meter() -> None:
    _seed(_settings(), "t-1", Usage(prompt_tokens=10))
    view = build_test_runtime(settings=Settings()).budget_status(thread_id="t-1")
    assert view.status == "disabled" and view.limits is None
    assert view.consumed is not None and view.consumed.tokens == 10


def test_budget_status_does_not_mutate_registry() -> None:
    settings = _settings()
    _seed(settings, "t-1", Usage(prompt_tokens=10))
    before = dict(_RUN_ENGINES)
    build_test_runtime(settings=settings).budget_status(thread_id="t-1")
    build_test_runtime(settings=settings).budget_status(thread_id="ghost")
    assert before == _RUN_ENGINES
