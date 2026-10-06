"""Decision value objects (Phase DEC — SPEC-DEC-TYP-001/002/003)."""

from __future__ import annotations

import dataclasses

import pytest

from prismal.core.config import Settings
from prismal.core.exceptions import DecisionBackendError, DecisionConfigError
from prismal.decisions.types import (
    Decision,
    DecisionKind,
    DecisionMode,
    GatePolicy,
    OptionSpec,
    Outcome,
    QuestionSpec,
    RiskTier,
)


def _choice(**kw: object) -> QuestionSpec:
    base: dict[str, object] = {
        "id": "route.supervisor",
        "kind": DecisionKind.CHOICE,
        "instructions": "Pick the best agent for the request.",
        "options": (OptionSpec("coder", "Writes code"), OptionSpec("researcher", "Searches")),
    }
    base.update(kw)
    return QuestionSpec(**base)  # type: ignore[arg-type]


def _score(levels: tuple[str, ...] = ("low", "mid", "high")) -> QuestionSpec:
    return QuestionSpec(
        id="eval.judge",
        kind=DecisionKind.SCORE,
        instructions="Rate faithfulness.",
        criteria="Is the answer supported by the context?",
        levels=levels,
    )


def _noul() -> QuestionSpec:
    return QuestionSpec(
        id="rag.chunk_relevance",
        kind=DecisionKind.NOUL,
        instructions="Is the chunk relevant to the query?",
        criteria="Relevant means it helps answer the query.",
    )


class TestQuestionSpec:
    def test_choice_needs_two_options(self) -> None:
        with pytest.raises(DecisionConfigError):
            _choice(options=(OptionSpec("a", "A"),))

    def test_choice_rejects_duplicate_ids(self) -> None:
        with pytest.raises(DecisionConfigError):
            _choice(options=(OptionSpec("a", "A"), OptionSpec("a", "B")))

    @pytest.mark.parametrize("n", [1, 11])
    def test_score_levels_between_2_and_10(self, n: int) -> None:
        with pytest.raises(DecisionConfigError):
            _score(tuple(f"l{i}" for i in range(n)))

    def test_score_accepts_bounds(self) -> None:
        assert len(_score(("a", "b")).levels) == 2
        assert len(_score(tuple(f"l{i}" for i in range(10))).levels) == 10

    def test_empty_id_rejected(self) -> None:
        with pytest.raises(DecisionConfigError):
            _choice(id="")

    def test_labels_per_kind(self) -> None:
        assert _choice().labels() == ("coder", "researcher")
        assert _score().labels() == ("low", "mid", "high")
        assert _noul().labels() == ("true", "false")

    def test_schema_hash_stable_and_sensitive(self) -> None:
        a, b = _choice(), _choice()
        assert a.schema_hash() == b.schema_hash()
        assert len(a.schema_hash()) == 64
        assert a.schema_hash() != _choice(instructions="Other.").schema_hash()
        assert a.schema_hash() != _choice(risk_tier=RiskTier.HIGH).schema_hash()

    def test_with_options_keeps_validation(self) -> None:
        q = _choice()
        flipped = q.with_options(tuple(reversed(q.options)))
        assert flipped.labels() == ("researcher", "coder")
        assert flipped.id == q.id


class TestRiskTier:
    def test_ordering(self) -> None:
        assert RiskTier.LOW.rank < RiskTier.MEDIUM.rank < RiskTier.HIGH.rank
        assert RiskTier.HIGH.rank < RiskTier.CRITICAL.rank
        assert RiskTier.MEDIUM.at_least(RiskTier.MEDIUM)
        assert not RiskTier.LOW.at_least(RiskTier.MEDIUM)


class TestDecision:
    def test_choice_from_probabilities(self) -> None:
        d = Decision.from_probabilities(
            _choice(),
            {"coder": 0.8, "researcher": 0.2},
            backend="fake",
            model="fake-1",
            spread_confidence=0.6,
            latency_ms=3.0,
        )
        assert d.value == "coder"
        assert d.answer_confidence == pytest.approx(0.8)
        assert d.margin == pytest.approx(0.6)
        assert d.spread_confidence == 0.6
        assert d.abstained is False
        assert d.outcome is Outcome.ESCALATED  # pending the gate
        assert d.expected_level is None

    def test_answer_confidence_never_from_vendor_confidence(self) -> None:
        d = Decision.from_probabilities(
            _choice(),
            {"coder": 0.55, "researcher": 0.45},
            backend="jev",
            model="jev-1.13.0",
            spread_confidence=0.99,
        )
        assert d.answer_confidence == pytest.approx(0.55)

    def test_probabilities_are_normalised(self) -> None:
        d = Decision.from_probabilities(
            _choice(), {"coder": 2.0, "researcher": 2.0}, backend="x", model="m"
        )
        assert sum(d.probabilities.values()) == pytest.approx(1.0)

    def test_noul_semantics(self) -> None:
        d = Decision.from_probabilities(_noul(), {"true": 0.3}, backend="x", model="m")
        assert d.value is False
        assert d.answer_confidence == pytest.approx(0.7)
        assert d.probabilities == pytest.approx({"true": 0.3, "false": 0.7})
        assert d.margin == pytest.approx(0.4)

    def test_noul_tie_is_true(self) -> None:
        d = Decision.from_probabilities(_noul(), {"true": 0.5}, backend="x", model="m")
        assert d.value is True

    def test_score_expected_level(self) -> None:
        d = Decision.from_probabilities(
            _score(), {"low": 0.0, "mid": 0.5, "high": 0.5}, backend="x", model="m"
        )
        assert d.expected_level == pytest.approx(0.75)
        assert d.value in {"mid", "high"}

    def test_unknown_label_is_backend_error(self) -> None:
        with pytest.raises(DecisionBackendError):
            Decision.from_probabilities(
                _choice(), {"coder": 0.5, "hacker": 0.5}, backend="x", model="m"
            )

    def test_empty_or_negative_probabilities_are_backend_errors(self) -> None:
        with pytest.raises(DecisionBackendError):
            Decision.from_probabilities(_choice(), {}, backend="x", model="m")
        with pytest.raises(DecisionBackendError):
            Decision.from_probabilities(
                _choice(), {"coder": -1.0, "researcher": 0.2}, backend="x", model="m"
            )

    def test_abstain(self) -> None:
        d = Decision.abstain(
            _choice(), backend="jev", model="jev-1.13.0", reason="timeout", latency_ms=800.0
        )
        assert d.abstained is True
        assert d.value is None
        assert d.outcome is Outcome.ABSTAINED
        assert d.reason == "timeout"
        assert d.answer_confidence == 0.0

    def test_frozen(self) -> None:
        d = Decision.abstain(_choice(), backend="x", model="m", reason="backend_error")
        with pytest.raises(dataclasses.FrozenInstanceError):
            d.value = "coder"  # type: ignore[misc]


class TestGatePolicy:
    def test_defaults(self) -> None:
        p = GatePolicy()
        assert p.tau(RiskTier.LOW) == 0.85
        assert p.tau(RiskTier.MEDIUM) == 0.90
        assert p.tau(RiskTier.HIGH) == 0.97
        assert p.tau(RiskTier.CRITICAL) is None
        assert p.min_margin == 0.20
        assert p.timeout_ms == 800
        assert p.confirm_floor == 0.50
        assert p.shuffle_check_tiers == frozenset({RiskTier.HIGH, RiskTier.CRITICAL})

    def test_from_settings_raise_only(self) -> None:
        s = Settings(
            decision_tau_overrides={"medium": 0.95},
            decision_timeout_ms=300,
            decision_min_margin=0.3,
        )
        p = GatePolicy.from_settings(s)
        assert p.tau(RiskTier.MEDIUM) == 0.95
        assert p.tau(RiskTier.LOW) == 0.85
        assert p.tau(RiskTier.CRITICAL) is None
        assert p.timeout_ms == 300
        assert p.min_margin == 0.3

    def test_with_tau_floor_never_lowers(self) -> None:
        p = GatePolicy()
        assert p.with_tau_floor(RiskTier.MEDIUM, 0.5).tau(RiskTier.MEDIUM) == 0.90
        assert p.with_tau_floor(RiskTier.MEDIUM, 0.93).tau(RiskTier.MEDIUM) == 0.93
        assert p.with_tau_floor(RiskTier.CRITICAL, 0.99).tau(RiskTier.CRITICAL) is None


def test_mode_values() -> None:
    assert {m.value for m in DecisionMode} == {"off", "shadow", "enforce"}
