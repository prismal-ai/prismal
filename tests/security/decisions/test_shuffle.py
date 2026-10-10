"""Option-order shuffle defeats first-option bias (Phase DEC — SPEC-DEC-CAS-003, DEC6-02)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from prismal.decisions.adapters.fake import FakeDecisionAdapter
from prismal.decisions.calibration import CalibrationRecord, CalibrationStore
from prismal.decisions.cascade import DecisionCascade
from prismal.decisions.state import DecisionStateBuilder
from prismal.decisions.types import (
    Decision,
    DecisionKind,
    GatePolicy,
    OptionSpec,
    Outcome,
    QuestionSpec,
    RiskTier,
)


def _q(tier: RiskTier, n: int = 4) -> QuestionSpec:
    return QuestionSpec(
        id="tool.proposal",
        kind=DecisionKind.CHOICE,
        instructions="Pick the tool.",
        options=tuple(OptionSpec(f"t{i}", f"Tool {i}") for i in range(n)),
        risk_tier=tier,
    )


class FirstOptionBias(FakeDecisionAdapter):
    """Always picks whichever option is listed first (the documented Jev/Laya bias)."""

    def __init__(self) -> None:
        super().__init__({}, name="laya", model="lm")
        self.orders: list[tuple[str, ...]] = []

    async def _answer(self, state: Any, questions: Any) -> dict[str, Decision]:
        out: dict[str, Decision] = {}
        for qid, q in questions.items():
            self.orders.append(q.labels())
            first = q.labels()[0]
            probs = {
                lab: (0.99 if lab == first else 0.01 / (len(q.labels()) - 1)) for lab in q.labels()
            }
            out[qid] = Decision.from_probabilities(q, probs, backend="laya", model="lm")
        return out


def _store(tmp_path: Path) -> CalibrationStore:
    store = CalibrationStore(tmp_path)
    store.save(
        CalibrationRecord.from_dict(
            {
                "decision_id": "tool.proposal",
                "model": "lm",
                "method": "temperature",
                "params": {"temperature": 1.0},
                "tau": 0.9,
                "target_precision": 0.95,
                "ece_before": 0.3,
                "ece_after": 0.02,
                "n_by_language": {"en": 900},
                "precision_by_language": {"en": 0.99},
                "version": "v1",
            }
        )
    )
    return store


@pytest.mark.parametrize("text", [f"request number {i}" for i in range(25)])
async def test_biased_backend_never_accepted_at_high(tmp_path: Path, text: str) -> None:
    backend = FirstOptionBias()
    cascade = DecisionCascade([backend], calibration=_store(tmp_path))
    q = _q(RiskTier.HIGH)
    st = DecisionStateBuilder().add_untrusted("user", text, source="user").build()
    d = (await cascade.decide(st, {q.id: q}, policy=GatePolicy(), integration="tool"))[q.id]
    assert d.outcome is Outcome.ESCALATED
    assert d.reason == "shuffle_inconsistent"
    assert len(backend.orders) == 2
    assert backend.orders[0] != backend.orders[1]  # the permuted request really differs


async def test_medium_choice_skips_the_second_request(tmp_path: Path) -> None:
    backend = FirstOptionBias()
    cascade = DecisionCascade([backend], calibration=_store(tmp_path))
    q = _q(RiskTier.MEDIUM)
    st = DecisionStateBuilder().add_untrusted("user", "x", source="user").build()
    await cascade.decide(st, {q.id: q}, policy=GatePolicy(), integration="tool")
    assert len(backend.orders) == 1
