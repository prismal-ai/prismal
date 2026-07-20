"""Unit tests for the Skynet S+ metered reducer (SPEC-SP-RED-001).

The default synthesis reducer records its LLM response into an injected shared
``CostMeter``; ``concat``/``first_success`` remain LLM-free and record nothing.
"""

from __future__ import annotations

from typing import Any

import pytest

from prismal.agents.skynet.reduce import reduce_results
from prismal.agents.skynet.types import WorkerResult
from prismal.budget.meter import CostMeter
from prismal.core.config import Settings


class _FakeResponse:
    def __init__(self, content: str, usage_metadata: dict[str, int] | None = None) -> None:
        self.content = content
        self.usage_metadata = usage_metadata


class MeteringProviderRegistry:
    """ProviderRegistry stand-in whose LLM returns a fixed usage_metadata."""

    def __init__(self, *, settings: Any = None) -> None:
        self._settings = settings

    def get_llm(self, *, model: str | None = None) -> Any:
        class _LLM:
            async def ainvoke(self, _messages: list[Any]) -> _FakeResponse:
                return _FakeResponse(
                    "synthesized", usage_metadata={"input_tokens": 50, "output_tokens": 20}
                )

        return _LLM()


def _results() -> list[WorkerResult]:
    return [
        WorkerResult(order_id="ord-1", output="a", success=True),
        WorkerResult(order_id="ord-2", output="b", success=True),
    ]


async def test_default_reducer_records_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default synthesis reducer records its response into the shared meter."""
    monkeypatch.setattr("prismal.providers.registry.ProviderRegistry", MeteringProviderRegistry)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    meter = CostMeter(settings=settings)

    answer = await reduce_results("goal", _results(), meter=meter, settings=settings)

    assert answer == "synthesized"
    assert meter.usage.total_tokens == 70
    assert meter.usage.calls == 1


async def test_concat_strategy_records_nothing() -> None:
    """concat is LLM-free — it records nothing into the meter."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    meter = CostMeter(settings=settings)

    answer = await reduce_results(
        "goal", _results(), strategy="concat", meter=meter, settings=settings
    )

    assert "[ord-1] a" in answer
    assert meter.usage.total_tokens == 0
    assert meter.usage.calls == 0


async def test_injected_reduce_fn_not_metered() -> None:
    """An injected reduce_fn owns its own metering — reduce_results does not double-count."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    meter = CostMeter(settings=settings)

    async def reduce_fn(_goal: str, _successes: list[WorkerResult]) -> str:
        return "custom"

    answer = await reduce_results(
        "goal", _results(), reduce_fn=reduce_fn, meter=meter, settings=settings
    )

    assert answer == "custom"
    assert meter.usage.total_tokens == 0
