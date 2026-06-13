"""Tests for cost computation (Phase C — SPEC-CST-COST-001).

``providers/cost.py`` is the only new module that imports ``litellm``; it tries
the LiteLLM native price first and falls back to a settings pricing table.
"""

from __future__ import annotations

import pytest

from prismal.core.config import Settings
from prismal.providers import cost as cost_mod
from prismal.providers.cost import CostEstimate, compute_cost_usd


def test_litellm_native_price_is_authoritative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cost_mod.litellm, "cost_per_token", lambda **kw: (0.001, 0.002), raising=True
    )
    est = compute_cost_usd("gpt-4o", 1000, 500)
    assert est.cost_usd == pytest.approx(0.003)
    assert est.source == "litellm"
    assert est.estimated is False


def test_falls_back_to_pricing_table(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**kw: object) -> tuple[float, float]:
        raise ValueError("model not mapped")

    monkeypatch.setattr(cost_mod.litellm, "cost_per_token", _boom, raising=True)
    settings = Settings(budget_pricing={"acme/model": {"input": 1.0, "output": 2.0}})
    # 1000 prompt @ $1/1k + 500 completion @ $2/1k = 1.0 + 1.0 = 2.0
    est = compute_cost_usd("acme/model", 1000, 500, settings=settings)
    assert est.cost_usd == pytest.approx(2.0)
    assert est.source == "table"
    assert est.estimated is True


def test_no_litellm_no_table_yields_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**kw: object) -> tuple[float, float]:
        raise ValueError("unknown")

    monkeypatch.setattr(cost_mod.litellm, "cost_per_token", _boom, raising=True)
    est = compute_cost_usd("mystery/model", 10, 10, settings=Settings())
    assert est == CostEstimate(0.0, "none")
    assert est.estimated is True


def test_never_raises_on_litellm_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**kw: object) -> tuple[float, float]:
        raise RuntimeError("boom")

    monkeypatch.setattr(cost_mod.litellm, "cost_per_token", _boom, raising=True)
    # No settings, no table — must still return a CostEstimate, not raise.
    est = compute_cost_usd("x", 1, 1)
    assert isinstance(est, CostEstimate)
    assert est.source == "none"
