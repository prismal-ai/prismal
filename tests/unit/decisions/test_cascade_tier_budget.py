"""Per-backend time budget (review M4): a slow local tier must not starve the next tier.

A backend exposing ``budget_ms`` (the Ollama sub-tiers) gets its own timeout and
does not consume the shared non-LLM budget (``decision_timeout_ms``).
"""

from __future__ import annotations

import asyncio
from typing import Any

from prismal.decisions.adapters.fake import FakeDecisionAdapter
from prismal.decisions.cascade import DecisionCascade
from prismal.decisions.state import DecisionStateBuilder
from prismal.decisions.types import Decision, DecisionKind, GatePolicy, QuestionSpec

Q = QuestionSpec(id="route.supervisor.x", kind=DecisionKind.NOUL, instructions="Urgent?")


class Slow(FakeDecisionAdapter):
    def __init__(self, delay_s: float, *, name: str, budget_ms: int | None = None) -> None:
        super().__init__({}, name=name, model=name)
        self.delay_s = delay_s
        self.seen_timeouts: list[int] = []
        if budget_ms is not None:
            self.budget_ms = budget_ms

    async def decide(self, state: Any, questions: Any, *, policy: GatePolicy) -> Any:
        self.seen_timeouts.append(policy.timeout_ms)
        return await super().decide(state, questions, policy=policy)

    async def _answer(self, state: Any, questions: Any) -> dict[str, Decision]:
        await asyncio.sleep(self.delay_s)
        return {
            qid: Decision.abstain(q, backend=self.name, model=self.model, reason="unsupported")
            for qid, q in questions.items()
        }


def _state() -> Any:
    return DecisionStateBuilder().add_untrusted("t", "hola", source="user").build()


async def test_own_budget_tier_gets_its_budget_and_keeps_the_shared_one() -> None:
    local = Slow(0.15, name="ollama:fast", budget_ms=1_000)
    remote = Slow(0.0, name="jev")
    await DecisionCascade([local, remote]).decide(
        _state(), {Q.id: Q}, policy=GatePolicy(timeout_ms=100), integration="route"
    )
    assert local.seen_timeouts == [1_000]
    assert remote.seen_timeouts and remote.seen_timeouts[0] > 50  # not starved by the local tier


async def test_tiers_without_a_budget_still_share_the_deadline() -> None:
    first = Slow(0.15, name="laya")
    second = Slow(0.0, name="jev")
    await DecisionCascade([first, second]).decide(
        _state(), {Q.id: Q}, policy=GatePolicy(timeout_ms=100), integration="route"
    )
    assert len(first.seen_timeouts) == 1 and 50 < first.seen_timeouts[0] <= 100
    assert second.seen_timeouts == []  # shared budget exhausted ⇒ abstained without a call
