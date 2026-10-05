"""CostMeter cache-aware metering (Phase TKE — SPEC-TKE-ACC-004)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from prismal.budget.meter import CostMeter
from prismal.core.config import Settings
from prismal.providers.cost import compute_cost_usd


class _Otel:
    def __init__(self) -> None:
        self.counters: list[tuple[str, int, dict[str, Any]]] = []
        self.histograms: list[tuple[str, float, dict[str, Any]]] = []

    def increment_counter(self, name: str, value: int = 1, attributes: Any = None) -> None:
        self.counters.append((name, value, attributes or {}))

    def record_histogram(self, name: str, value: float, attributes: Any = None) -> None:
        self.histograms.append((name, value, attributes or {}))


@pytest.fixture
def otel(monkeypatch: pytest.MonkeyPatch) -> _Otel:
    fake = _Otel()
    monkeypatch.setattr("prismal.monitoring.otel.OTelManager", lambda: fake)
    return fake


def _msg() -> SimpleNamespace:
    return SimpleNamespace(
        usage_metadata={
            "input_tokens": 1000,
            "output_tokens": 100,
            "input_token_details": {"cache_read": 800, "cache_creation": 50},
            "output_token_details": {"reasoning": 30},
        },
        response_metadata={},
    )


def _anthropic_raw() -> SimpleNamespace:
    return SimpleNamespace(
        usage_metadata=None,
        response_metadata={
            "token_usage": {
                "prompt_tokens": 50,
                "completion_tokens": 10,
                "cache_read_input_tokens": 4000,
                "cache_creation_input_tokens": 300,
            }
        },
    )


def test_master_off_prices_with_v3_12_arguments(otel: _Otel) -> None:
    meter = CostMeter(settings=Settings())
    usage = meter.record_response(_msg(), "claude-sonnet-4-5", agent="coder")
    assert usage.cost_usd == compute_cost_usd("claude-sonnet-4-5", 1000, 100).cost_usd
    assert usage.cache_read_tokens == 0
    names = {c[0] for c in otel.counters}
    assert "llm_input_tokens" not in names
    assert "llm_reasoning_tokens" not in names


def test_master_off_never_normalizes_anthropic_raw(otel: _Otel) -> None:
    usage = CostMeter(settings=Settings()).record_response(_anthropic_raw(), "claude-sonnet-4-5")
    assert usage.prompt_tokens == 50


def test_master_on_prices_cache_tokens(otel: _Otel) -> None:
    meter = CostMeter(settings=Settings(token_efficiency_enabled=True))
    usage = meter.record_response(_msg(), "claude-sonnet-4-5", agent="coder")
    expected = compute_cost_usd(
        "claude-sonnet-4-5", 1000, 100, cache_read_tokens=800, cache_write_tokens=50
    )
    assert usage.cost_usd == pytest.approx(expected.cost_usd)
    assert (usage.cache_read_tokens, usage.cache_write_tokens, usage.reasoning_tokens) == (
        800,
        50,
        30,
    )
    assert usage.cost_source == "litellm"


def test_master_on_normalizes_anthropic_raw(otel: _Otel) -> None:
    meter = CostMeter(settings=Settings(token_efficiency_enabled=True))
    usage = meter.record_response(_anthropic_raw(), "claude-sonnet-4-5")
    assert usage.prompt_tokens == 4350


def test_master_on_emits_kind_split_and_hit_ratio(otel: _Otel) -> None:
    meter = CostMeter(settings=Settings(token_efficiency_enabled=True))
    meter.record_response(_msg(), "claude-sonnet-4-5", agent="coder")
    split = {c[2]["kind"]: c[1] for c in otel.counters if c[0] == "llm_input_tokens"}
    assert split == {"uncached": 150, "cache_read": 800, "cache_write": 50}
    assert ("llm_reasoning_tokens", 30, {"agent": "coder", "model": "claude-sonnet-4-5"}) in (
        otel.counters
    )
    ratios = [h for h in otel.histograms if h[0] == "llm_cache_hit_ratio"]
    assert ratios == [("llm_cache_hit_ratio", 0.8, {"agent": "coder"})]


def test_reported_cost_passes_through(otel: _Otel) -> None:
    meter = CostMeter(settings=Settings())
    usage = meter.record_response(_msg(), "x/unknown", reported_cost_usd=0.07)
    assert usage.cost_usd == 0.07
    assert usage.estimated is False
    assert usage.cost_source == "gateway"
