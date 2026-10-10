"""Gateway cost precedence (Phase GW — SPEC-GW-COST-001, ADR-WAVE-001 §2.3, GW5-01)."""

from __future__ import annotations

import pytest

from prismal.core.config import Settings
from prismal.providers.cost import CostEstimate, compute_cost_usd

_TABLE = Settings(budget_pricing={"house/model": {"input": 1.0, "output": 2.0}})


def test_default_mode_unchanged() -> None:
    assert compute_cost_usd("house/model", 1000, 1000, settings=_TABLE) == CostEstimate(
        3.0, "table"
    )
    assert compute_cost_usd("house/model", 1000, 1000, settings=_TABLE).equivalent_usd == 0.0


def test_gateway_reported_wins_in_metered_mode() -> None:
    est = compute_cost_usd("house/model", 1000, 1000, settings=_TABLE, reported_cost_usd=0.5)
    assert (est.cost_usd, est.source, est.equivalent_usd) == (0.5, "gateway", 0.0)
    assert est.estimated is False


def test_subscription_with_reported_cost_uses_it_as_equivalent() -> None:
    est = compute_cost_usd(
        "house/model", 1000, 1000, settings=_TABLE, reported_cost_usd=0.5, cost_mode="subscription"
    )
    assert (est.cost_usd, est.source, est.equivalent_usd) == (0.0, "subscription", 0.5)


def test_subscription_without_reported_prices_from_table() -> None:
    est = compute_cost_usd("house/model", 1000, 1000, settings=_TABLE, cost_mode="subscription")
    assert (est.cost_usd, est.source) == (0.0, "subscription")
    assert est.equivalent_usd == pytest.approx(3.0)


def test_subscription_prices_from_litellm_when_known() -> None:
    est = compute_cost_usd("gpt-4o", 1000, 1000, cost_mode="subscription")
    litellm_price = compute_cost_usd("gpt-4o", 1000, 1000)
    assert est.source == "subscription" and est.cost_usd == 0.0
    assert litellm_price.source == "litellm"
    assert est.equivalent_usd == pytest.approx(litellm_price.cost_usd)


def test_subscription_unknown_model_equivalent_zero() -> None:
    est = compute_cost_usd("unknown/zzz", 10, 10, cost_mode="subscription")
    assert (est.cost_usd, est.source, est.equivalent_usd) == (0.0, "subscription", 0.0)


def test_subscription_is_not_an_estimate() -> None:
    assert compute_cost_usd("unknown/zzz", 1, 1, cost_mode="subscription").estimated is False
