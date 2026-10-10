"""DEC-EVAL judge cascade (Phase DEC — SPEC-DEC-INT-002, DEC4-03/04)."""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import pytest

from prismal.core.config import Settings
from prismal.decisions.adapters.fake import FakeDecisionAdapter
from prismal.decisions.calibration import CalibrationRecord, CalibrationStore
from prismal.decisions.cascade import DecisionCascade
from prismal.decisions.integrations.eval_judge import (
    EVAL_DECISION_ID,
    LLM_ONLY_TAGS,
    EvalSample,
    Rubric,
    build_eval_question,
    judge_with_cascade,
)
from prismal.decisions.shadow import ShadowRunner, reset_demotions
from prismal.decisions.types import Decision, DecisionMode


@pytest.fixture(autouse=True)
def _reset() -> Any:
    reset_demotions()
    yield
    reset_demotions()


class LlmJudge:
    def __init__(self, score: float = 0.25) -> None:
        self.calls = 0
        self._score = score

    async def score(self, *, rubric: str, answer: str, context: str = "") -> float:
        self.calls += 1
        return self._score


RUBRIC = Rubric(id="faithfulness", text="The answer is supported by the context.")
SAMPLE = EvalSample(answer="Paris is the capital of France.", context="France's capital is Paris.")


def _cascade(
    tmp_path: Path, level: str, p: float = 0.97
) -> tuple[DecisionCascade, FakeDecisionAdapter]:
    q = build_eval_question(RUBRIC)

    def pick(_s: Any) -> Decision:
        labels = q.labels()
        rest = (1 - p) / (len(labels) - 1)
        return Decision.from_probabilities(
            q, {lab: (p if lab == level else rest) for lab in labels}, backend="x", model="x"
        )

    store = CalibrationStore(tmp_path)
    store.save(
        CalibrationRecord.from_dict(
            {
                "decision_id": EVAL_DECISION_ID,
                "model": "lm",
                "method": "temperature",
                "params": {"temperature": 1.0},
                "tau": 0.9,
                "target_precision": 0.95,
                "ece_before": 0.3,
                "ece_after": 0.02,
                "n_by_language": {"en": 900, "es": 900},
                "precision_by_language": {"en": 0.99, "es": 0.99},
                "version": "v1",
            }
        )
    )
    laya = FakeDecisionAdapter({EVAL_DECISION_ID: pick}, name="laya", model="lm")
    return DecisionCascade([laya], calibration=store), laya


def _settings(**kw: Any) -> Settings:
    base = Settings().model_dump()
    base.update(decisions_enabled=True, decision_eval_judge_enabled=True, **kw)
    return Settings.model_construct(**base)


async def test_accepted_decision_replaces_llm_judge(tmp_path: Path) -> None:
    cascade, _ = _cascade(tmp_path, "excellent")
    judge = LlmJudge()
    s = _settings(decision_modes={"eval_judge": "enforce"}, decision_eval_audit_rate=0.0)
    result = await judge_with_cascade(SAMPLE, RUBRIC, cascade=cascade, llm_judge=judge, settings=s)
    assert result.source == "decision"
    assert result.score == 1.0
    assert judge.calls == 0


@pytest.mark.parametrize("tag", sorted(LLM_ONLY_TAGS))
async def test_math_code_logic_rubrics_llm_only(tmp_path: Path, tag: str) -> None:
    cascade, laya = _cascade(tmp_path, "excellent")
    judge = LlmJudge(0.4)
    rubric = Rubric(id="r", text="Correct result.", tags=frozenset({tag}))
    s = _settings(decision_modes={"eval_judge": "enforce"})
    result = await judge_with_cascade(SAMPLE, rubric, cascade=cascade, llm_judge=judge, settings=s)
    assert result.source == "llm"
    assert result.score == 0.4
    assert laya.calls == []


async def test_escalation_falls_back_to_llm(tmp_path: Path) -> None:
    cascade, _ = _cascade(tmp_path, "good", p=0.4)
    judge = LlmJudge(0.6)
    s = _settings(decision_modes={"eval_judge": "enforce"})
    result = await judge_with_cascade(SAMPLE, RUBRIC, cascade=cascade, llm_judge=judge, settings=s)
    assert result.source == "llm"
    assert result.score == 0.6


async def test_audit_rate_rejudges_and_feeds_drift(tmp_path: Path) -> None:
    cascade, _ = _cascade(tmp_path, "excellent")
    judge = LlmJudge(0.0)  # strongly disagrees
    s = _settings(
        decision_modes={"eval_judge": "enforce"},
        decision_eval_audit_rate=1.0,
        decision_auto_demote=True,
        decision_demote_floor=0.9,
    )
    runner = ShadowRunner(cascade, settings=s, demote_min_window=1)
    result = await judge_with_cascade(
        SAMPLE,
        RUBRIC,
        cascade=cascade,
        llm_judge=judge,
        settings=s,
        shadow=runner,
        rng=random.Random(0),
    )
    assert result.source == "decision"
    assert result.audited is True
    assert result.drift == pytest.approx(1.0)
    assert judge.calls == 1
    from prismal.decisions.shadow import is_demoted

    assert is_demoted("eval_judge")


async def test_shadow_returns_llm_score(tmp_path: Path) -> None:
    cascade, laya = _cascade(tmp_path, "excellent")
    judge = LlmJudge(0.5)
    s = _settings(decision_modes={"eval_judge": "shadow"})
    runner = ShadowRunner(cascade, settings=s)
    result = await judge_with_cascade(
        SAMPLE, RUBRIC, cascade=cascade, llm_judge=judge, settings=s, shadow=runner
    )
    assert result.source == "llm"
    assert result.score == 0.5
    await result.wait_shadow()
    assert runner.report("eval_judge").n == 1


async def test_disabled_is_llm_only(tmp_path: Path) -> None:
    cascade, laya = _cascade(tmp_path, "excellent")
    judge = LlmJudge(0.3)
    result = await judge_with_cascade(
        SAMPLE, RUBRIC, cascade=cascade, llm_judge=judge, settings=Settings()
    )
    assert result.source == "llm"
    assert laya.calls == []


async def test_forbidden_rubric_text_falls_back_to_llm(tmp_path: Path) -> None:
    cascade, laya = _cascade(tmp_path, "excellent")
    judge = LlmJudge(0.7)
    rubric = Rubric(id="r", text="Count how many facts are cited.")
    s = _settings(decision_modes={"eval_judge": "enforce"})
    result = await judge_with_cascade(SAMPLE, rubric, cascade=cascade, llm_judge=judge, settings=s)
    assert result.source == "llm"
    assert laya.calls == []


def test_question_levels_ordered_low_to_high() -> None:
    q = build_eval_question(RUBRIC)
    assert q.levels[0] == "fail"
    assert q.levels[-1] == "excellent"
    assert q.criteria == RUBRIC.text
    assert DecisionMode.ENFORCE.value == "enforce"
