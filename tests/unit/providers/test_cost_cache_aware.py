"""Cache-aware pricing (Phase TKE — SPEC-TKE-ACC-003; ADR-WAVE-001 §2.3)."""

from __future__ import annotations

from typing import Any

import litellm
import pytest
from litellm.types.utils import ModelResponse

from prismal.core.config import Settings
from prismal.providers import cost as cost_mod
from prismal.providers.cost import CostEstimate, compute_cost_usd


def test_defaults_reproduce_v3_12_estimate() -> None:
    expected_prompt, expected_completion = litellm.cost_per_token(
        model="claude-sonnet-4-5", prompt_tokens=1000, completion_tokens=100
    )
    est = compute_cost_usd("claude-sonnet-4-5", 1000, 100)
    assert est == CostEstimate(expected_prompt + expected_completion, "litellm")


def test_cache_read_tokens_are_priced_at_the_cache_rate() -> None:
    full = compute_cost_usd("claude-sonnet-4-5", 1000, 100)
    cached = compute_cost_usd("claude-sonnet-4-5", 1000, 100, cache_read_tokens=800)
    assert cached.source == "litellm"
    assert cached.cost_usd < full.cost_usd
    assert cached.cost_usd == pytest.approx(0.00084 + 0.0015)


def test_reported_cost_wins_and_is_not_estimated() -> None:
    est = compute_cost_usd("anything/unknown", 10, 10, reported_cost_usd=0.0123)
    assert est == CostEstimate(0.0123, "gateway")
    assert est.estimated is False


def test_litellm_is_not_estimated_table_is() -> None:
    assert CostEstimate(1.0, "litellm").estimated is False
    assert CostEstimate(1.0, "table").estimated is True
    assert CostEstimate(0.0, "none").estimated is True


def test_raw_response_uses_completion_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_completion_cost(*, completion_response: object, **_: Any) -> float:
        seen["resp"] = completion_response
        return 0.5

    monkeypatch.setattr(cost_mod.litellm, "completion_cost", fake_completion_cost)
    resp = ModelResponse()
    est = compute_cost_usd("claude-sonnet-4-5", 1, 1, response=resp)
    assert est == CostEstimate(0.5, "litellm")
    assert seen["resp"] is resp


def test_non_litellm_response_object_is_ignored() -> None:
    plain = compute_cost_usd("claude-sonnet-4-5", 1000, 100)
    assert compute_cost_usd("claude-sonnet-4-5", 1000, 100, response=object()) == plain


def test_completion_cost_failure_falls_through(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(**_: Any) -> float:
        raise RuntimeError("no price")

    monkeypatch.setattr(cost_mod.litellm, "completion_cost", boom)
    est = compute_cost_usd("claude-sonnet-4-5", 1000, 100, response=ModelResponse())
    assert est == compute_cost_usd("claude-sonnet-4-5", 1000, 100)


def test_table_prices_cache_tokens() -> None:
    settings = Settings(
        budget_pricing={
            "my/model": {"input": 1.0, "output": 2.0, "cache_read": 0.1, "cache_write": 1.25}
        }
    )
    est = compute_cost_usd(
        "my/model", 1000, 1000, settings=settings, cache_read_tokens=500, cache_write_tokens=100
    )
    # uncached 400 * 1.0/1k + read 500 * 0.1/1k + write 100 * 1.25/1k + out 1000 * 2/1k
    assert est.source == "table"
    assert est.cost_usd == pytest.approx(0.4 + 0.05 + 0.125 + 2.0)


def test_table_cache_falls_back_to_input_rate() -> None:
    settings = Settings(budget_pricing={"my/model": {"input": 1.0, "output": 0.0}})
    est = compute_cost_usd("my/model", 1000, 0, settings=settings, cache_read_tokens=500)
    assert est.cost_usd == pytest.approx(1.0)


def test_table_default_kwargs_unchanged() -> None:
    settings = Settings(budget_pricing={"my/model": {"input": 1.0, "output": 2.0}})
    assert compute_cost_usd("my/model", 1000, 1000, settings=settings) == CostEstimate(3.0, "table")
