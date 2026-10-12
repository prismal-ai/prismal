"""DEC-EVAL — eval-judge cascade (Phase DEC — SPEC-DEC-INT-002).

A Score decision replaces the LLM judge for rubrics the models handle well
(faithfulness, relevance, tone). Rubrics tagged ``math`` / ``code`` / ``logic``
are LLM-only (typed models cannot count or compute). A sample of accepted items
(``decision_eval_audit_rate``, default 5 %) is re-judged by the LLM; the
agreement feeds the drift alarm (SHD-002).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

from prismal.core.exceptions import ForbiddenDecisionError
from prismal.decisions.integrations._common import ShadowHandle, accepted, safe_decide
from prismal.decisions.shadow import effective_mode
from prismal.decisions.state import DecisionStateBuilder
from prismal.decisions.types import (
    Decision,
    DecisionKind,
    DecisionMode,
    GatePolicy,
    QuestionSpec,
    RiskTier,
)
from prismal.decisions.validation import validate_question

if TYPE_CHECKING:
    from prismal.core.config import Settings
    from prismal.decisions.cascade import DecisionCascade
    from prismal.decisions.shadow import ShadowRunner

INTEGRATION = "eval_judge"
EVAL_DECISION_ID = "eval.judge"
LLM_ONLY_TAGS: frozenset[str] = frozenset({"math", "code", "logic"})
_DEFAULT_LEVELS = ("fail", "weak", "partial", "good", "excellent")
_DRIFT_TOLERANCE = 0.25  # |decision - LLM| within one level of a 5-level scale


class ScoringJudge(Protocol):
    """The LLM judge (:class:`prismal.eval.judges.Judge` conforms)."""

    async def score(self, *, rubric: str, answer: str, context: str = "") -> float:
        """Score *answer* against *rubric* in ``[0, 1]``."""
        ...


@dataclass(frozen=True)
class Rubric:
    """A developer-authored rubric; ``tags`` drive the LLM-only rule."""

    id: str
    text: str
    tags: frozenset[str] = frozenset()
    levels: tuple[str, ...] = _DEFAULT_LEVELS


@dataclass(frozen=True)
class EvalSample:
    """The (untrusted) item under evaluation."""

    answer: str
    context: str = ""
    question: str = ""


@dataclass
class JudgeResult:
    """Score in ``[0, 1]`` and where it came from."""

    score: float
    source: Literal["decision", "llm"]
    decision: Decision | None = None
    audited: bool = False
    drift: float | None = None
    _shadow: ShadowHandle | None = field(default=None, repr=False)

    async def wait_shadow(self) -> None:
        """Wait until the shadow comparison (if any) has been recorded."""
        if self._shadow is not None:
            await self._shadow.wait()


def build_eval_question(rubric: Rubric) -> QuestionSpec:
    """Low-risk Score question rating an answer against *rubric* on its levels."""
    return QuestionSpec(
        id=EVAL_DECISION_ID,
        kind=DecisionKind.SCORE,
        instructions="Rate how well the answer in the state satisfies the rubric.",
        criteria=rubric.text,
        levels=rubric.levels,
        risk_tier=RiskTier.LOW,
    )


def _level_score(level: str, levels: tuple[str, ...]) -> float:
    return levels.index(level) / (len(levels) - 1)


def _nearest_level(score: float, levels: tuple[str, ...]) -> str:
    return levels[round(max(0.0, min(1.0, score)) * (len(levels) - 1))]


async def _llm(judge: ScoringJudge, sample: EvalSample, rubric: Rubric) -> float:
    return float(
        await judge.score(rubric=rubric.text, answer=sample.answer, context=sample.context)
    )


async def judge_with_cascade(
    sample: EvalSample,
    rubric: Rubric,
    *,
    cascade: DecisionCascade,
    llm_judge: ScoringJudge,
    settings: Settings,
    shadow: ShadowRunner | None = None,
    rng: random.Random | None = None,
    policy: GatePolicy | None = None,
) -> JudgeResult:
    """Score ``sample`` with the cascade where allowed, else with the LLM judge."""
    mode = effective_mode(settings, INTEGRATION)
    question = build_eval_question(rubric)
    if mode is DecisionMode.OFF or rubric.tags & LLM_ONLY_TAGS:
        return JudgeResult(score=await _llm(llm_judge, sample, rubric), source="llm")
    try:
        validate_question(question, integration=INTEGRATION)
    except ForbiddenDecisionError:
        return JudgeResult(score=await _llm(llm_judge, sample, rubric), source="llm")

    builder = DecisionStateBuilder().add_untrusted("answer", sample.answer, source="eval_answer")
    if sample.context:
        builder.add_untrusted("context", sample.context, source="eval_context")
    if sample.question:
        builder.add_untrusted("question", sample.question, source="eval_question")
    state = builder.build()
    gate = policy or GatePolicy.from_settings(settings)

    if mode is DecisionMode.SHADOW:
        handle = None
        if shadow is not None:
            task = shadow.launch(
                state, {question.id: question}, policy=gate, integration=INTEGRATION
            )
            handle = ShadowHandle(
                shadow,
                task,
                integration=INTEGRATION,
                question_id=question.id,
                language=state.language,
            )
        score = await _llm(llm_judge, sample, rubric)
        if handle is not None:
            handle.record_reference(_nearest_level(score, rubric.levels))
        return JudgeResult(score=score, source="llm", _shadow=handle)

    decisions = await safe_decide(
        cascade, state, {question.id: question}, policy=gate, integration=INTEGRATION, mode=mode
    )
    d = decisions.get(question.id)
    if not accepted(d) or not isinstance(d.value, str):
        return JudgeResult(score=await _llm(llm_judge, sample, rubric), source="llm", decision=d)
    score = _level_score(d.value, rubric.levels)
    result = JudgeResult(score=score, source="decision", decision=d)
    sampler = rng or random.Random()  # noqa: S311 # nosec B311 — audit sampling
    if (
        settings.decision_eval_audit_rate > 0
        and sampler.random() < settings.decision_eval_audit_rate
    ):
        reference = await _llm(llm_judge, sample, rubric)
        result.audited = True
        result.drift = abs(reference - score)
        if shadow is not None:
            shadow.record_audit(INTEGRATION, agree=result.drift <= _DRIFT_TOLERANCE)
    return result
