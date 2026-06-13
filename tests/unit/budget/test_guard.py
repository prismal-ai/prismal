"""Tests for the BudgetGuard (Phase C — SPEC-CST-GRD-001)."""

from __future__ import annotations

import pytest

from prismal.budget.guard import BudgetGuard, make_budget_guard_fn
from prismal.budget.meter import CostMeter
from prismal.budget.types import Budget, Usage
from prismal.core.exceptions import BudgetExceeded


def _guard(budget: Budget, used: Usage, **kw: object) -> BudgetGuard:
    meter = CostMeter()
    meter.record(used)
    return BudgetGuard(budget, meter, **kw)  # type: ignore[arg-type]


# ── check(): within / soft / hard per dimension ───────────────────────────────


def test_within_when_under_soft_ratio() -> None:
    g = _guard(Budget(max_tokens=100), Usage(prompt_tokens=50), soft_ratio=0.8)
    st = g.check()
    assert st.within is True
    assert st.soft_exceeded is False
    assert st.hard_exceeded is False
    assert st.breached_dimension is None


def test_soft_at_ratio_threshold() -> None:
    g = _guard(Budget(max_tokens=100), Usage(prompt_tokens=80), soft_ratio=0.8)
    st = g.check()
    assert st.soft_exceeded is True
    assert st.hard_exceeded is False
    assert st.within is False
    assert st.breached_dimension == "tokens"


def test_hard_at_limit() -> None:
    g = _guard(Budget(max_tokens=100), Usage(prompt_tokens=100))
    st = g.check()
    assert st.hard_exceeded is True
    assert st.breached_dimension == "tokens"


def test_unlimited_budget_never_trips() -> None:
    g = _guard(Budget(), Usage(prompt_tokens=10_000_000, cost_usd=999.0, calls=999))
    st = g.check()
    assert st.within is True


def test_cost_dimension_trips() -> None:
    g = _guard(Budget(max_cost_usd=1.0), Usage(cost_usd=1.0))
    assert g.check().hard_exceeded is True
    assert g.check().breached_dimension == "cost"


def test_calls_dimension_trips() -> None:
    g = _guard(Budget(max_calls=5), Usage(calls=5))
    assert g.check().breached_dimension == "calls"


def test_wall_clock_dimension_trips() -> None:
    g = _guard(Budget(max_wall_clock_s=30.0), Usage(wall_clock_s=30.0))
    assert g.check().breached_dimension == "wall_clock"


def test_breached_dimension_is_highest_ratio() -> None:
    """When several dimensions trip, the worst (highest ratio) is reported."""
    g = _guard(
        Budget(max_tokens=100, max_calls=10),
        Usage(prompt_tokens=90, calls=100),  # tokens 0.9, calls 10.0
    )
    assert g.check().breached_dimension == "calls"


# ── enforce(): audit + raise / warn ───────────────────────────────────────────


class _FakeAudit:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def log_event(self, event_type: str, payload: dict[str, object]) -> None:
        self.events.append((event_type, payload))


def test_enforce_raises_on_hard_cap() -> None:
    audit = _FakeAudit()
    g = _guard(Budget(max_tokens=100), Usage(prompt_tokens=120), audit=audit, hard_cap=True)
    with pytest.raises(BudgetExceeded) as exc:
        g.enforce()
    assert exc.value.dimension == "tokens"
    assert exc.value.used == 120
    assert exc.value.limit == 100
    assert audit.events
    assert audit.events[0][0] == "budget_cutoff"
    assert audit.events[0][1]["action"] == "abort"


def test_enforce_soft_only_when_hard_cap_disabled() -> None:
    audit = _FakeAudit()
    g = _guard(Budget(max_tokens=100), Usage(prompt_tokens=120), audit=audit, hard_cap=False)
    st = g.enforce()  # must NOT raise
    assert st.hard_exceeded is True
    assert audit.events[0][1]["action"] == "abort"


def test_enforce_soft_audits_degrade() -> None:
    audit = _FakeAudit()
    g = _guard(Budget(max_tokens=100), Usage(prompt_tokens=85), audit=audit, soft_ratio=0.8)
    st = g.enforce()
    assert st.soft_exceeded is True
    assert audit.events[0][1]["action"] == "degrade"


def test_audit_is_hash_first_counts_only() -> None:
    """Cutoff payload carries dimension/used/limit/scope/action — no user content."""
    audit = _FakeAudit()
    g = _guard(Budget(max_tokens=100), Usage(prompt_tokens=120), audit=audit)
    with pytest.raises(BudgetExceeded):
        g.enforce()
    payload = audit.events[0][1]
    assert set(payload) >= {"dimension", "used", "limit", "scope", "action"}


# ── degradation() ─────────────────────────────────────────────────────────────


def test_degradation_terminate_on_hard() -> None:
    g = _guard(Budget(max_tokens=100), Usage(prompt_tokens=120))
    d = g.degradation()
    assert d.terminate is True
    assert d.reduce is False


def test_degradation_reduce_on_soft() -> None:
    g = _guard(Budget(max_tokens=100), Usage(prompt_tokens=85), soft_ratio=0.8)
    d = g.degradation()
    assert d.reduce is True
    assert d.terminate is False


def test_degradation_none_when_within() -> None:
    g = _guard(Budget(max_tokens=100), Usage(prompt_tokens=10))
    d = g.degradation()
    assert d.terminate is False
    assert d.reduce is False


# ── make_budget_guard_fn() adapter ────────────────────────────────────────────


async def test_guard_fn_none_always_true() -> None:
    fn = make_budget_guard_fn(None)
    assert await fn({}) is True


async def test_guard_fn_true_when_within() -> None:
    g = _guard(Budget(max_tokens=100), Usage(prompt_tokens=10))
    fn = make_budget_guard_fn(g)
    assert await fn({}) is True


async def test_guard_fn_false_on_soft() -> None:
    g = _guard(Budget(max_tokens=100), Usage(prompt_tokens=85), soft_ratio=0.8)
    fn = make_budget_guard_fn(g)
    assert await fn({}) is False


async def test_guard_fn_raises_on_hard_cap() -> None:
    g = _guard(Budget(max_tokens=100), Usage(prompt_tokens=120), hard_cap=True)
    fn = make_budget_guard_fn(g)
    with pytest.raises(BudgetExceeded):
        await fn({})


async def test_guard_fn_false_on_hard_when_soft_only() -> None:
    g = _guard(Budget(max_tokens=100), Usage(prompt_tokens=120), hard_cap=False)
    fn = make_budget_guard_fn(g)
    assert await fn({}) is False
