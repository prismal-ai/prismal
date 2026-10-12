"""Jev ``/systemone`` wire mapping shared by the HTTP, SDK and Laya adapters.

Shapes verified in DEC0-01 (``docs/decisions.md#verified-facts``): every
question type carries ``criteria`` — Choice ``{option_id: description}``, Score
an ordered list of level descriptions, Noul none (our criteria text is folded
into ``instructions``). Answers: Choice ``probabilities`` by option id; Score
``probabilities`` keyed by level index (``"0"``, ``"1"``, …); Noul ``noul`` =
P(true) with no ``confidence``. Unknown extra fields are ignored.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prismal.core.exceptions import DecisionBackendError
from prismal.decisions._telemetry import inc
from prismal.decisions.types import Decision, DecisionKind

if TYPE_CHECKING:
    from collections.abc import Mapping

    from prismal.decisions.types import QuestionSpec


def wire_question(q: QuestionSpec) -> dict[str, Any]:
    """Developer-authored question → Jev wire object (no untrusted text ever lands here)."""
    instructions = q.instructions
    if q.criteria:
        instructions = f"{instructions}\n\nCriteria: {q.criteria}"
    out: dict[str, Any] = {"type": q.kind.value, "instructions": instructions}
    if q.kind is DecisionKind.CHOICE:
        out["criteria"] = {o.id: o.description for o in q.options}
    elif q.kind is DecisionKind.SCORE:
        out["criteria"] = list(q.levels)
    return out


def wire_questions(questions: Mapping[str, QuestionSpec]) -> dict[str, Any]:
    """Map question ids to their Jev wire objects."""
    return {qid: wire_question(q) for qid, q in questions.items()}


def parse_answer(
    q: QuestionSpec,
    answer: Any,
    *,
    backend: str,
    model: str,
    latency_ms: float,
    confidence_key: str = "confidence",
) -> Decision:
    """One wire answer → pending :class:`Decision`; malformed ⇒ ``DecisionBackendError``."""
    if not isinstance(answer, dict):
        raise DecisionBackendError(f"answer for {q.id!r} is not an object")
    if q.kind is DecisionKind.NOUL:
        raw = answer.get("noul", answer.get("probability"))
        if raw is None and isinstance(answer.get("probabilities"), dict):
            raw = answer["probabilities"].get("true")
        if not isinstance(raw, int | float) or isinstance(raw, bool):
            raise DecisionBackendError(f"noul answer for {q.id!r} has no probability")
        p = float(raw)
        if not 0.0 <= p <= 1.0:
            raise DecisionBackendError(f"noul probability out of range for {q.id!r}")
        spread = answer.get(confidence_key)
        return Decision.from_probabilities(
            q,
            {"true": p},
            backend=backend,
            model=model,
            spread_confidence=float(spread) if isinstance(spread, int | float) else abs(2 * p - 1),
            latency_ms=latency_ms,
        )
    probs = answer.get("probabilities")
    if not isinstance(probs, dict) or not probs:
        raise DecisionBackendError(f"answer for {q.id!r} has no probabilities")
    if q.kind is DecisionKind.SCORE:
        mapped: dict[str, float] = {}
        for key, value in probs.items():
            label = _level_label(q, key)
            mapped[label] = float(value)
        probs = mapped
    spread = answer.get(confidence_key)
    return Decision.from_probabilities(
        q,
        {str(k): float(v) for k, v in probs.items()},
        backend=backend,
        model=model,
        spread_confidence=float(spread) if isinstance(spread, int | float) else 0.0,
        latency_ms=latency_ms,
    )


def _level_label(q: QuestionSpec, key: object) -> str:
    text = str(key)
    if text in q.levels:
        return text
    try:
        index = int(text)
    except ValueError as exc:
        raise DecisionBackendError(f"unknown score level {text!r} for {q.id!r}") from exc
    if not 0 <= index < len(q.levels):
        raise DecisionBackendError(f"score level {index} out of range for {q.id!r}")
    return q.levels[index]


def parse_answers(
    questions: Mapping[str, QuestionSpec],
    answers: Any,
    *,
    backend: str,
    model: str,
    latency_ms: float,
    confidence_key: str = "confidence",
) -> dict[str, Decision]:
    """Parse every answer; a malformed or missing one abstains only that question."""
    answers = answers if isinstance(answers, dict) else {}
    out: dict[str, Decision] = {}
    for qid, q in questions.items():
        try:
            out[qid] = parse_answer(
                q,
                answers.get(qid),
                backend=backend,
                model=model,
                latency_ms=latency_ms,
                confidence_key=confidence_key,
            )
        except (DecisionBackendError, TypeError, ValueError):
            out[qid] = Decision.abstain(
                q, backend=backend, model=model, reason="backend_error", latency_ms=latency_ms
            )
    return out


def parse_payload(
    payload: Any,
    questions: Mapping[str, QuestionSpec],
    *,
    backend: str,
    model: str,
    latency_ms: float,
) -> dict[str, Decision] | None:
    """Parse a ``/systemone`` response body; ``None`` when the served model is not pinned."""
    if not isinstance(payload, dict):
        raise DecisionBackendError(f"{backend} returned a non-object body")
    if payload.get("model") != model:
        return None
    return parse_answers(
        questions, payload.get("answers"), backend=backend, model=model, latency_ms=latency_ms
    )


def count_model_mismatch(backend: str) -> None:
    """Increment ``prismal.decision_model_mismatch_total{backend}`` (fail-open)."""
    inc("decision_model_mismatch", {"backend": backend})
