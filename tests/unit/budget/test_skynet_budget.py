"""Skynet budget unification (Phase C — C7-01).

The dormant ``skynet_token_budget`` / ``SkynetBudgetExceeded`` are now enforced
through the shared budget engine: the supervisor meters its planner/evaluator
LLM calls and raises ``SkynetBudgetExceeded`` (a ``BudgetExceeded``) at the round
boundary when the token budget is exceeded.
"""

from __future__ import annotations

import pytest

from prismal.agents.skynet.supervisor import SkynetSupervisor
from prismal.budget.types import Usage
from prismal.core.config import Settings
from prismal.core.exceptions import BudgetExceeded, SkynetBudgetExceeded


def _sup(token_budget: int, **kw: object) -> SkynetSupervisor:
    return SkynetSupervisor(settings=Settings(skynet_token_budget=token_budget), **kw)  # type: ignore[arg-type]


def test_supervisor_exposes_meter() -> None:
    sup = _sup(1000)
    sup.meter.record(Usage(prompt_tokens=10))
    assert sup.meter.usage.total_tokens == 10


def test_enforce_raises_when_over_budget() -> None:
    sup = _sup(1000)
    sup.meter.record(Usage(prompt_tokens=1500))
    with pytest.raises(SkynetBudgetExceeded) as exc:
        sup.enforce_token_budget()
    assert exc.value.dimension == "tokens"
    assert exc.value.used == 1500
    assert exc.value.limit == 1000


def test_skynet_budget_is_a_budget_exceeded() -> None:
    sup = _sup(100)
    sup.meter.record(Usage(prompt_tokens=200))
    with pytest.raises(BudgetExceeded):
        sup.enforce_token_budget()


def test_enforce_noop_when_unlimited() -> None:
    sup = _sup(0)  # 0 = unlimited
    sup.meter.record(Usage(prompt_tokens=10_000))
    sup.enforce_token_budget()  # must not raise


def test_enforce_noop_under_budget() -> None:
    sup = _sup(1000)
    sup.meter.record(Usage(prompt_tokens=500))
    sup.enforce_token_budget()  # must not raise


async def test_evaluate_enforces_budget_before_running() -> None:
    calls = {"eval": 0}

    async def fake_eval(messages: list[dict[str, str]]) -> tuple[bool, str]:
        calls["eval"] += 1
        return (True, "done")

    sup = SkynetSupervisor(settings=Settings(skynet_token_budget=100), evaluate_fn=fake_eval)
    sup.meter.record(Usage(prompt_tokens=500))  # already over budget

    with pytest.raises(SkynetBudgetExceeded):
        await sup.evaluate("goal", [])
    assert calls["eval"] == 0  # aborted before the evaluator LLM call
