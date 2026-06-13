"""Tests for the CostMeter (Phase C — SPEC-CST-MET-001)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from prismal.budget.meter import CostMeter
from prismal.budget.types import Usage
from prismal.core.config import Settings


def test_record_accumulates() -> None:
    meter = CostMeter()
    meter.record(Usage(prompt_tokens=10, completion_tokens=5, cost_usd=0.01, calls=1))
    meter.record(Usage(prompt_tokens=3, completion_tokens=2, cost_usd=0.02, calls=1))
    assert meter.usage.total_tokens == 20
    assert meter.usage.cost_usd == pytest.approx(0.03)
    assert meter.usage.calls == 2


def test_record_returns_new_cumulative() -> None:
    meter = CostMeter()
    snap = meter.record(Usage(calls=1, cost_usd=0.5))
    assert snap.calls == 1
    assert snap is not None
    assert meter.usage.calls == 1


def test_record_response_meters_a_message() -> None:
    """extract + cost (via the pricing table) + accumulate, counting one call."""
    settings = Settings(budget_pricing={"fake/model": {"input": 1.0, "output": 2.0}})
    meter = CostMeter(settings=settings)
    msg = SimpleNamespace(usage_metadata={"input_tokens": 1000, "output_tokens": 500})

    usage = meter.record_response(msg, "fake/model", wall_clock_s=1.25)

    assert usage.prompt_tokens == 1000
    assert usage.completion_tokens == 500
    assert usage.cost_usd == pytest.approx(2.0)  # 1.0 + (0.5 * 2.0)
    assert usage.calls == 1
    assert usage.wall_clock_s == pytest.approx(1.25)
    assert usage.estimated is True  # table fallback
    assert meter.usage.calls == 1


def test_record_response_bridges_to_cost_tracker() -> None:
    """When a CostTracker + session_id are present, usage is persisted too."""
    calls: list[dict[str, object]] = []

    class _FakeTracker:
        def record(  # noqa: PLR0913
            self,
            session_id: str,
            model: str,
            tokens_in: int,
            tokens_out: int,
            cost_usd: float,
            user_id: str | None = None,
        ) -> str:
            calls.append(
                {
                    "session_id": session_id,
                    "model": model,
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "cost_usd": cost_usd,
                    "user_id": user_id,
                }
            )
            return "entry-1"

    settings = Settings(budget_pricing={"fake/model": {"input": 1.0, "output": 0.0}})
    meter = CostMeter(
        settings=settings,
        cost_tracker=_FakeTracker(),
        session_id="sess-1",
        tenant="org-9",
    )
    msg = SimpleNamespace(usage_metadata={"input_tokens": 1000, "output_tokens": 0})
    meter.record_response(msg, "fake/model")

    assert len(calls) == 1
    assert calls[0]["session_id"] == "sess-1"
    assert calls[0]["tokens_in"] == 1000
    assert calls[0]["cost_usd"] == pytest.approx(1.0)
    assert calls[0]["user_id"] == "org-9"


def test_no_tracker_means_no_bridge() -> None:
    """Without a tracker the meter still accumulates (in-memory only)."""
    meter = CostMeter()
    msg = SimpleNamespace(usage_metadata={"input_tokens": 5, "output_tokens": 5})
    meter.record_response(msg, "fake/model")
    assert meter.usage.total_tokens == 10
