"""``DecisionCascade.decide(observe=...)`` sees every raw tier answer (review H1).

The HITL gate needs every tier's answer, not only the accepted one: a raise from
one tier must survive a later tier's accepted "no" (SPEC-DEC-OLL-012/014).
"""

from __future__ import annotations

from typing import Any

from prismal.decisions.adapters.fake import FakeDecisionAdapter
from prismal.decisions.cascade import DecisionCascade
from prismal.decisions.state import DecisionStateBuilder
from prismal.decisions.types import Decision, DecisionKind, GatePolicy, QuestionSpec

Q = QuestionSpec(id="hitl.health_safety", kind=DecisionKind.NOUL, instructions="Health risk?")


def _ans(p: float, backend: str) -> Decision:
    return Decision.from_probabilities(Q, {"true": p}, backend=backend, model=backend)


def _state() -> Any:
    return DecisionStateBuilder().add_untrusted("summary", "hola", source="action").build()


async def test_observe_receives_every_tier_answer_in_order() -> None:
    first = FakeDecisionAdapter({Q.id: _ans(0.95, "ollama:accurate")}, name="ollama:accurate")
    llm = FakeDecisionAdapter({Q.id: _ans(0.05, "llm")}, name="llm", model="gpt")
    seen: list[tuple[str, float]] = []
    await DecisionCascade([first, llm]).decide(
        _state(),
        {Q.id: Q},
        policy=GatePolicy(),
        integration="hitl",
        observe=lambda d: seen.append((d.backend, d.probabilities["true"])),
    )
    assert seen == [("ollama:accurate", 0.95), ("llm", 0.05)]


async def test_observe_skips_abstentions_and_never_breaks_the_cascade() -> None:
    first = FakeDecisionAdapter({}, name="laya")  # no answer ⇒ abstains

    def boom(_: Decision) -> None:
        raise RuntimeError("observer bug")

    llm = FakeDecisionAdapter({Q.id: _ans(0.05, "llm")}, name="llm", model="gpt")
    out = await DecisionCascade([first, llm]).decide(
        _state(), {Q.id: Q}, policy=GatePolicy(), integration="hitl", observe=boom
    )
    assert out[Q.id].backend == "llm"
