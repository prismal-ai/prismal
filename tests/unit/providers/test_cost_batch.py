"""Batch-price metering in ``compute_cost_usd`` (AMR5-05 — ADR-WAVE-001 §2.3)."""

from __future__ import annotations

import pytest

from prismal.core.config import Settings
from prismal.providers import cost as cost_mod
from prismal.providers.cost import compute_cost_usd


def _litellm_price(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cost_mod.litellm, "cost_per_token", lambda **kw: (0.001, 0.002))


def _no_litellm(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**kw: object) -> tuple[float, float]:
        raise ValueError("unmapped")

    monkeypatch.setattr(cost_mod.litellm, "cost_per_token", _boom)


def test_batch_litellm_price_times_ratio(monkeypatch: pytest.MonkeyPatch) -> None:
    _litellm_price(monkeypatch)
    est = compute_cost_usd("gpt-4o", 1000, 500, settings=Settings(), batch=True)
    assert est.cost_usd == pytest.approx(0.0015)
    assert est.source == "litellm"


def test_batch_ratio_comes_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    _litellm_price(monkeypatch)
    est = compute_cost_usd("gpt-4o", 1, 1, settings=Settings(batch_price_ratio=0.25), batch=True)
    assert est.cost_usd == pytest.approx(0.00075)


def test_batch_table_price_times_ratio(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_litellm(monkeypatch)
    settings = Settings(budget_pricing={"acme/m": {"input": 1.0, "output": 2.0}})
    est = compute_cost_usd("acme/m", 1000, 500, settings=settings, batch=True)
    assert est.cost_usd == pytest.approx(1.0)
    assert est.source == "table"


def test_batch_ignored_for_gateway_reported_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    _litellm_price(monkeypatch)
    est = compute_cost_usd("m", 1, 1, settings=Settings(), reported_cost_usd=0.4, batch=True)
    assert est.cost_usd == pytest.approx(0.4)
    assert est.source == "gateway"


def test_batch_subscription_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    _litellm_price(monkeypatch)
    plain = compute_cost_usd("m", 1000, 500, settings=Settings(), cost_mode="subscription")
    batched = compute_cost_usd(
        "m", 1000, 500, settings=Settings(), cost_mode="subscription", batch=True
    )
    assert batched == plain


def test_batch_none_source_stays_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_litellm(monkeypatch)
    est = compute_cost_usd("mystery", 10, 10, settings=Settings(), batch=True)
    assert est.cost_usd == 0.0
    assert est.source == "none"


def test_batch_without_settings_uses_default_ratio(monkeypatch: pytest.MonkeyPatch) -> None:
    _litellm_price(monkeypatch)
    est = compute_cost_usd("gpt-4o", 1000, 500, batch=True)
    assert est.cost_usd == pytest.approx(0.0015)


def test_batch_false_default_equals_previous(monkeypatch: pytest.MonkeyPatch) -> None:
    _litellm_price(monkeypatch)
    assert compute_cost_usd("gpt-4o", 1000, 500, batch=False) == compute_cost_usd(
        "gpt-4o", 1000, 500
    )
