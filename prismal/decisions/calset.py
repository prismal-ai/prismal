"""Calibration-set tooling (Phase DEC — DEC3-07, CAL-002).

Turns hand-labelled cases into the two files ``python -m prismal.decisions
calibrate`` consumes, asking a backend the **production** question over the
**production** state:

* :func:`resolve_question` returns the exact :class:`QuestionSpec` an
  integration asks (same instructions, options and order), so a calibration
  record describes what the model sees in production.
* :func:`build_case_state` builds the decision state with the integration's own
  builder (``build_route_state`` / ``build_rag_state``): same fences, same
  sanitizer, same language detection, same hash.
* :func:`collect` asks one question per request and keeps the case order;
  abstentions are dropped from *both* outputs so they stay aligned.

Case file format (one JSON object per line)::

    {
        "id": "route-0001",
        "decision_id": "route.supervisor",
        "input": {"user": "...", "history": [{"role": "human", "content": "..."}]},
        "label": "coder",
        "language": "es",
        "tags": ["clear"],
        "notes": "...",
    }

``rag.chunk_relevance`` cases use ``"input": {"query": ..., "chunk": ...}`` and a
boolean label. Never put production data in a case file: synthesize or anonymize.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prismal.decisions.types import GatePolicy

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from prismal.decisions.ports import DecisionPort
    from prismal.decisions.state import DecisionState
    from prismal.decisions.types import Decision, QuestionSpec

#: Languages a calibration slice may be recorded for (``decision_languages`` default).
CASE_LANGUAGES = frozenset({"es", "en"})
ROUTES_PROFILES = ("base", "advanced")


# ── questions ─────────────────────────────────────────────────────────────────


def _route_question(routes_profile: str) -> QuestionSpec:
    from prismal.agents.supervisor import effective_valid_routes
    from prismal.decisions.integrations.route import build_route_question

    if routes_profile not in ROUTES_PROFILES:
        raise ValueError(
            f"unknown routes profile {routes_profile!r}; expected one of {ROUTES_PROFILES}"
        )
    return build_route_question(effective_valid_routes(routes_profile == "advanced"))


def _rag_question(routes_profile: str) -> QuestionSpec:
    del routes_profile
    from prismal.decisions.integrations.rag import RAG_QUESTION

    return RAG_QUESTION


#: decision_id → production question factory. Extend together with
#: :data:`_STATE_BUILDERS` when another integration gets a calibration set.
_QUESTIONS: dict[str, Callable[[str], QuestionSpec]] = {
    "route.supervisor": _route_question,
    "rag.chunk_relevance": _rag_question,
}
SUPPORTED_DECISIONS: tuple[str, ...] = tuple(_QUESTIONS)


def resolve_question(decision_id: str, *, routes_profile: str = "base") -> QuestionSpec:
    """The exact question the integration asks in production.

    ``routes_profile`` only matters for ``route.supervisor``: ``base`` is the 15
    core routes plus ``END`` (``enable_subgraphs`` off), ``advanced`` adds the
    pattern and domain-pipeline routes.
    """
    factory = _QUESTIONS.get(decision_id)
    if factory is None:
        raise ValueError(
            f"decision {decision_id!r} is not supported yet; supported: {SUPPORTED_DECISIONS}"
        )
    return factory(routes_profile)


def question_sha256(question: QuestionSpec) -> str:
    """Fingerprint of everything the model reads from the question."""
    canonical = {
        "id": question.id,
        "kind": question.kind.value,
        "instructions": question.instructions,
        "criteria": question.criteria,
        "options": [[o.id, o.description] for o in question.options],
        "levels": list(question.levels),
        "risk_tier": question.risk_tier.value,
    }
    blob = json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ── cases ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CalCase:
    """One hand-labelled case of a calibration set."""

    id: str
    decision_id: str
    input: Mapping[str, Any]
    label: str
    language: str
    tags: tuple[str, ...] = ()
    notes: str = ""


def _label(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def load_cases(path: str | Path) -> list[CalCase]:
    """Read a case file; raises ``ValueError`` naming the first malformed line."""
    out: list[CalCase] = []
    for lineno, line in enumerate(Path(path).read_text("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            out.append(
                CalCase(
                    id=str(row["id"]),
                    decision_id=str(row["decision_id"]),
                    input=dict(row.get("input") or {}),
                    label=_label(row["label"]),
                    language=str(row.get("language") or ""),
                    tags=tuple(str(t) for t in row.get("tags") or ()),
                    notes=str(row.get("notes") or ""),
                )
            )
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ValueError(f"{path}: malformed case on line {lineno}") from exc
    return out


def _route_state(case: CalCase) -> DecisionState:
    from langchain_core.messages import AIMessage, HumanMessage

    from prismal.decisions.integrations.route import build_route_state

    user = case.input.get("user")
    if not isinstance(user, str) or not user.strip():
        raise ValueError("input.user must be a non-empty string")
    messages: list[Any] = []
    for turn in case.input.get("history") or ():
        role, content = turn.get("role"), turn.get("content")
        if role not in ("human", "ai") or not isinstance(content, str):
            raise ValueError("input.history turns need role human|ai and string content")
        messages.append(HumanMessage(content=content) if role == "human" else AIMessage(content))
    messages.append(HumanMessage(content=user))
    return build_route_state(messages)


def _rag_state(case: CalCase) -> DecisionState:
    from prismal.decisions.integrations.rag import build_rag_state

    query, chunk = case.input.get("query"), case.input.get("chunk")
    if not (isinstance(query, str) and query.strip() and isinstance(chunk, str) and chunk.strip()):
        raise ValueError("input.query and input.chunk must be non-empty strings")
    return build_rag_state(query, chunk)


_STATE_BUILDERS: dict[str, Callable[[CalCase], DecisionState]] = {
    "route.supervisor": _route_state,
    "rag.chunk_relevance": _rag_state,
}


def build_case_state(case: CalCase) -> DecisionState:
    """The decision state production would build for this case."""
    builder = _STATE_BUILDERS.get(case.decision_id)
    if builder is None:
        raise ValueError(f"no state builder for decision {case.decision_id!r}")
    return builder(case)


@dataclass
class CaseReport:
    """Result of :func:`check_cases`: blocking errors, warnings and the balance."""

    n: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    by_label: Counter[str] = field(default_factory=Counter)
    by_language: dict[str, int] = field(default_factory=dict)
    by_tag: Counter[str] = field(default_factory=Counter)

    def to_dict(self) -> dict[str, object]:
        """JSON-friendly view (labels and tags sorted by count)."""
        return {
            "n": self.n,
            "errors": self.errors,
            "warnings": self.warnings,
            "by_label": dict(self.by_label.most_common()),
            "by_language": self.by_language,
            "by_tag": dict(self.by_tag.most_common()),
        }


def check_cases(cases: Sequence[CalCase], question: QuestionSpec) -> CaseReport:
    """Validate a case set against the production question.

    Errors (the set must not be used): wrong ``decision_id``, label outside the
    question's labels, duplicate ids, invalid input, unsupported language.
    Warnings: the language production would *detect* differs from the declared
    one (the enforce gate uses the detected language), or an answer label has no
    case at all.
    """
    report = CaseReport(n=len(cases))
    labels = question.labels()
    seen: set[str] = set()
    languages: Counter[str] = Counter()
    for case in cases:
        if case.id in seen:
            report.errors.append(f"duplicate id {case.id!r}")
        seen.add(case.id)
        if case.decision_id != question.id:
            report.errors.append(
                f"{case.id}: decision_id {case.decision_id!r} is not {question.id!r}"
            )
            continue
        if case.label not in labels:
            report.errors.append(f"{case.id}: label {case.label!r} is not one of {labels}")
        if case.language not in CASE_LANGUAGES:
            report.errors.append(
                f"{case.id}: language {case.language!r} not in {sorted(CASE_LANGUAGES)}"
            )
        try:
            state = build_case_state(case)
        except ValueError as exc:
            report.errors.append(f"{case.id}: input {exc}")
            continue
        if case.language in CASE_LANGUAGES and state.language != case.language:
            report.warnings.append(
                f"{case.id}: declared {case.language!r} but production detected {state.language!r}"
            )
        report.by_label[case.label] += 1
        languages[case.language] += 1
        report.by_tag.update(case.tags)
    report.by_language = dict(languages)
    for label in labels:
        if report.by_label[label] == 0:
            report.warnings.append(f"no cases for label {label!r}")
    return report


# ── collecting predictions ───────────────────────────────────────────────────


def _value_label(d: Decision) -> str | None:
    if d.value is None:
        return None
    return ("true" if d.value else "false") if isinstance(d.value, bool) else str(d.value)


def _percentile(samples: Sequence[float], q: float) -> float | None:
    if not samples:
        return None
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


@dataclass
class CollectResult:
    """Aligned labeled / prediction rows plus run statistics."""

    question: QuestionSpec
    backend: str
    model: str
    labeled: list[dict[str, Any]] = field(default_factory=list)
    predictions: list[dict[str, Any]] = field(default_factory=list)
    abstained: list[tuple[str, str]] = field(default_factory=list)
    confusion: Counter[tuple[str, str]] = field(default_factory=Counter)
    latencies_ms: list[float] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        """Share of non-abstained predictions whose top answer equals the label."""
        if not self.predictions:
            return 0.0
        hits = sum(n for (label, pred), n in self.confusion.items() if label == pred)
        return hits / len(self.predictions)

    def summary(self, *, n_cases: int, model_key: str) -> dict[str, Any]:
        """Run summary written next to the outputs."""
        per_label: dict[str, dict[str, float]] = {}
        totals: Counter[str] = Counter()
        hits: Counter[str] = Counter()
        for (label, pred), n in self.confusion.items():
            totals[label] += n
            if label == pred:
                hits[label] += n
        for label in sorted(totals):
            per_label[label] = {"n": totals[label], "accuracy": hits[label] / totals[label]}
        return {
            "decision_id": self.question.id,
            "question_sha256": question_sha256(self.question),
            "backend": self.backend,
            "model": model_key,
            "n_cases": n_cases,
            "n_predictions": len(self.predictions),
            "abstained": [{"id": cid, "reason": reason} for cid, reason in self.abstained],
            "accuracy": self.accuracy,
            "accuracy_by_label": per_label,
            "confusion": [
                {"label": label, "predicted": pred, "n": n}
                for (label, pred), n in sorted(self.confusion.items())
                if label != pred
            ],
            "latency_ms": {
                "p50": _percentile(self.latencies_ms, 0.5),
                "p95": _percentile(self.latencies_ms, 0.95),
            },
        }


async def collect(
    cases: Sequence[CalCase],
    question: QuestionSpec,
    backend: DecisionPort,
    *,
    policy: GatePolicy | None = None,
    timeout_ms: int = 60_000,
    on_progress: Callable[[int, int], None] | None = None,
) -> CollectResult:
    """Ask ``backend`` the production question for every case, one per request.

    The returned rows are raw (uncalibrated) distributions in case order;
    abstained cases are left out of both lists and listed in ``abstained``.
    ``timeout_ms`` replaces the 800 ms gate default: a slow answer must count as
    data, otherwise the hard (slow) cases would be dropped and bias the set.
    """
    gate = policy or GatePolicy(timeout_ms=timeout_ms)
    result = CollectResult(question=question, backend=backend.name, model=backend.model)
    for index, case in enumerate(cases, start=1):
        state = build_case_state(case)
        decided = await backend.decide(dict(state.fields), {question.id: question}, policy=gate)
        d = decided.get(question.id)
        if d is None or d.abstained or not d.probabilities:
            reason = "missing" if d is None else (d.reason or "abstained")
            result.abstained.append((case.id, reason))
        else:
            result.labeled.append(
                {
                    "case_id": case.id,
                    "state": dict(case.input) | {"_sha256": state.sha256},
                    "question_id": question.id,
                    "label": case.label,
                    "language": case.language,
                }
            )
            result.predictions.append(
                {
                    "case_id": case.id,
                    "question_id": question.id,
                    "kind": question.kind.value,
                    "probabilities": {k: float(v) for k, v in d.probabilities.items()},
                    "confidence": d.spread_confidence,
                    "backend": d.backend,
                    "latency_ms": d.latency_ms,
                }
            )
            result.confusion[(case.label, _value_label(d) or "")] += 1
            result.latencies_ms.append(d.latency_ms)
        if on_progress is not None:
            on_progress(index, len(cases))
    return result


@dataclass(frozen=True)
class CollectPaths:
    """Where :func:`write_collect` put its files."""

    labeled: Path
    predictions: Path
    summary: Path


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def write_collect(result: CollectResult, out_dir: str | Path, *, model_key: str) -> CollectPaths:
    """Write ``<id>@<model_key>.{labeled,preds}.jsonl`` and ``.summary.json``.

    ``model_key`` is the pinned model, e.g. ``tev1:4b@9b5bb969e46c``; pass the
    same value to ``calibrate --model``.
    """
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{result.question.id}@{model_key}"
    paths = CollectPaths(
        labeled=directory / f"{stem}.labeled.jsonl",
        predictions=directory / f"{stem}.preds.jsonl",
        summary=directory / f"{stem}.summary.json",
    )

    def dump(rows: list[dict[str, Any]]) -> str:
        return "".join(json.dumps(_jsonable(r), ensure_ascii=False) + "\n" for r in rows)

    paths.labeled.write_text(dump(result.labeled), "utf-8")
    paths.predictions.write_text(dump(result.predictions), "utf-8")
    n_cases = len(result.labeled) + len(result.abstained)
    summary = result.summary(n_cases=n_cases, model_key=model_key)
    paths.summary.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", "utf-8")
    return paths
