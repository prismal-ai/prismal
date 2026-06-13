"""react_loop budget integration (Phase C — C6-01 / SPEC-CST-INT-001)."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from prismal.agents.tool_registry import react_loop
from prismal.budget.guard import BudgetGuard
from prismal.budget.meter import CostMeter
from prismal.budget.types import Budget, Usage


class _FakeLLM:
    """A chat model stub that records call count and returns a fixed response."""

    def __init__(self, response: object) -> None:
        self._response = response
        self.calls = 0
        self.model = "fake/model"

    async def ainvoke(self, messages: object) -> object:
        self.calls += 1
        return self._response


def _final_response() -> AIMessage:
    msg = AIMessage(content="final answer")
    msg.usage_metadata = {"input_tokens": 10, "output_tokens": 5}  # type: ignore[attr-defined]
    return msg


def _guard(budget: Budget, *, prefill: Usage | None = None) -> BudgetGuard:
    meter = CostMeter()
    if prefill is not None:
        meter.record(prefill)
    return BudgetGuard(budget, meter)


# ── metering ──────────────────────────────────────────────────────────────────


async def test_react_loop_meters_each_call() -> None:
    guard = _guard(Budget())  # unlimited — never trips
    llm = _FakeLLM(_final_response())

    await react_loop(llm, [], [HumanMessage(content="hi")], budget_guard=guard)

    assert llm.calls == 1
    assert guard.meter.usage.calls == 1
    assert guard.meter.usage.total_tokens == 15


# ── hard cap stops further calls (graceful partial, no raise) ─────────────────


async def test_hard_cap_returns_partial_without_calling_llm() -> None:
    # Budget already exhausted before the loop starts.
    guard = _guard(Budget(max_tokens=100), prefill=Usage(prompt_tokens=200))
    llm = _FakeLLM(_final_response())

    result = await react_loop(llm, [], [HumanMessage(content="hi")], budget_guard=guard)

    assert llm.calls == 0  # the loop never calls the model
    assert isinstance(result, AIMessage)
    assert "budget" in str(result.content).lower()


# ── soft cap does NOT stop the loop ───────────────────────────────────────────


async def test_soft_cap_does_not_stop_loop() -> None:
    guard = _guard(Budget(max_tokens=100), prefill=Usage(prompt_tokens=80))  # 0.8 soft
    llm = _FakeLLM(_final_response())

    await react_loop(llm, [], [HumanMessage(content="hi")], budget_guard=guard)

    assert llm.calls == 1  # soft cap warns but proceeds


# ── backward compatibility ────────────────────────────────────────────────────


async def test_no_guard_is_unchanged() -> None:
    llm = _FakeLLM(_final_response())
    result = await react_loop(llm, [], [HumanMessage(content="hi")])
    assert llm.calls == 1
    assert result.content == "final answer"  # type: ignore[union-attr]
