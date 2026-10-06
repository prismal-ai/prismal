"""Gate evaluation and option-order shuffle (Phase DEC — SPEC-DEC-CAS-002/003)."""

from __future__ import annotations

import pytest

from prismal.decisions.gate import evaluate_gate, shuffle_options, shuffle_required
from prismal.decisions.types import (
    Decision,
    DecisionKind,
    GatePolicy,
    OptionSpec,
    QuestionSpec,
    RiskTier,
)

POLICY = GatePolicy()


def _q(tier: RiskTier = RiskTier.MEDIUM, kind: DecisionKind = DecisionKind.CHOICE) -> QuestionSpec:
    if kind is DecisionKind.NOUL:
        return QuestionSpec(id="q", kind=kind, instructions="Is it relevant?", risk_tier=tier)
    return QuestionSpec(
        id="q",
        kind=kind,
        instructions="Pick one.",
        options=tuple(OptionSpec(f"o{i}", f"Option {i}") for i in range(5)),
        risk_tier=tier,
    )


def _d(q: QuestionSpec, top: float) -> Decision:
    labels = q.labels()
    rest = (1.0 - top) / (len(labels) - 1)
    probs = {lab: (top if i == 0 else rest) for i, lab in enumerate(labels)}
    return Decision.from_probabilities(q, probs, backend="laya", model="m")


def test_accepts_confident_calibrated_untainted() -> None:
    q = _q()
    assert evaluate_gate(_d(q, 0.95), q, POLICY, calibrated=True, tainted=False) == (True, "")


def test_abstained_never_accepted() -> None:
    q = _q()
    d = Decision.abstain(q, backend="jev", model="m", reason="timeout")
    assert evaluate_gate(d, q, POLICY, calibrated=True, tainted=False) == (False, "timeout")


def test_uncalibrated_rejected() -> None:
    q = _q()
    assert evaluate_gate(_d(q, 0.99), q, POLICY, calibrated=False, tainted=False) == (
        False,
        "uncalibrated",
    )


@pytest.mark.parametrize("corroborated", [False, True])
def test_critical_never_accepted(corroborated: bool) -> None:
    q = _q(RiskTier.CRITICAL)
    ok, reason = evaluate_gate(
        _d(q, 1.0),
        q,
        POLICY,
        calibrated=True,
        tainted=False,
        corroborated=corroborated,
        shuffle_consistent=True,
    )
    assert ok is False
    assert reason == "below_tau"


def test_below_tau_and_low_margin() -> None:
    q = _q()
    assert evaluate_gate(_d(q, 0.89), q, POLICY, calibrated=True, tainted=False) == (
        False,
        "below_tau",
    )
    q2 = _q(RiskTier.LOW, DecisionKind.NOUL)
    # P(true)=0.59 → conf 0.59 < τ low 0.85
    d = Decision.from_probabilities(q2, {"true": 0.59}, backend="laya", model="m")
    assert evaluate_gate(d, q2, POLICY, calibrated=True, tainted=False)[1] == "below_tau"
    loose = GatePolicy(min_margin=0.5, tau_accept={RiskTier.MEDIUM: 0.5})
    d3 = Decision.from_probabilities(
        q, {"o0": 0.6, "o1": 0.4, "o2": 0.0, "o3": 0.0, "o4": 0.0}, backend="laya", model="m"
    )
    assert evaluate_gate(d3, q, loose, calibrated=True, tainted=False) == (False, "low_margin")


def test_tainted_medium_needs_corroboration() -> None:
    q = _q()
    d = _d(q, 0.99)
    assert evaluate_gate(d, q, POLICY, calibrated=True, tainted=True) == (False, "tainted")
    assert evaluate_gate(d, q, POLICY, calibrated=True, tainted=True, corroborated=True) == (
        True,
        "",
    )


def test_tainted_low_tier_may_accept() -> None:
    q = _q(RiskTier.LOW)
    assert evaluate_gate(_d(q, 0.99), q, POLICY, calibrated=True, tainted=True) == (True, "")


def test_high_choice_requires_shuffle_consistency() -> None:
    q = _q(RiskTier.HIGH)
    d = _d(q, 0.99)
    assert shuffle_required(q, POLICY)
    assert evaluate_gate(d, q, POLICY, calibrated=True, tainted=False)[1] == (
        "shuffle_inconsistent"
    )
    assert evaluate_gate(d, q, POLICY, calibrated=True, tainted=False, shuffle_consistent=False)[
        1
    ] == ("shuffle_inconsistent")
    assert evaluate_gate(d, q, POLICY, calibrated=True, tainted=False, shuffle_consistent=True) == (
        True,
        "",
    )


def test_shuffle_not_required_for_noul_or_medium() -> None:
    assert not shuffle_required(_q(RiskTier.HIGH, DecisionKind.NOUL), POLICY)
    assert not shuffle_required(_q(RiskTier.MEDIUM), POLICY)


def test_gate_never_raises_on_garbage() -> None:
    q = _q()
    bad = Decision.abstain(q, backend="x", model="m", reason="")
    ok, reason = evaluate_gate(bad, q, POLICY, calibrated=True, tainted=False)
    assert ok is False
    assert reason == "backend_error"


class TestShuffle:
    def test_deterministic_permutation_of_ids(self) -> None:
        q = _q()
        a = shuffle_options(q, seed=42)
        b = shuffle_options(q, seed=42)
        assert a.labels() == b.labels()
        assert sorted(a.labels()) == sorted(q.labels())
        assert a.labels() != q.labels()

    @pytest.mark.parametrize("seed", range(50))
    def test_no_option_keeps_its_position(self, seed: int) -> None:
        q = _q()
        permuted = shuffle_options(q, seed=seed)
        assert all(a != b for a, b in zip(permuted.labels(), q.labels(), strict=True))

    def test_two_options_are_swapped(self) -> None:
        q = QuestionSpec(
            id="q",
            kind=DecisionKind.CHOICE,
            instructions="Pick.",
            options=(OptionSpec("a", "A"), OptionSpec("b", "B")),
        )
        for seed in range(10):
            assert shuffle_options(q, seed=seed).labels() == ("b", "a")

    def test_non_choice_unchanged(self) -> None:
        q = _q(kind=DecisionKind.NOUL)
        assert shuffle_options(q, seed=1) == q
