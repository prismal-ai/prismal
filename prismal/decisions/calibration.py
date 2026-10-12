"""Calibration records, store and startup enforcement (Phase DEC — SPEC-DEC-CAL-001..003).

Files ``<decision_calibration_dir>/<decision_id>@<model>.json`` are versioned,
code-reviewed artifacts (DD-DEC-006). A record is model-pinned: when the pinned
model changes, the old file simply no longer matches and enforce is refused.

Key format (SPEC-DEC-OLL-003): Laya / Jev models are plain pinned names
(``route.supervisor@jev-1.13.0.json``). Ollama sub-tier backends report
``model = "<name>@<digest12>"`` (first 12 hex chars of the ``/api/tags`` digest),
so their records are ``<decision_id>@<name>@<digest12>.json``. When the local
model is re-pulled its digest changes, the exact key no longer matches and the
decision runs uncalibrated (shadow-only: never accepted, it escalates); the
cascade reports that as *stale* (``decision_calibration_stale``).
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from prismal.core.exceptions import DecisionCalibrationError
from prismal.core.logging import get_logger
from prismal.decisions.catalog import (
    INTEGRATION_DECISION_IDS,
    INTEGRATION_FLAGS,
    backend_models,
    configured_mode,
)
from prismal.decisions.types import Decision

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from prismal.core.config import Settings
    from prismal.decisions.types import QuestionSpec

logger = get_logger(__name__)

#: ``<name>@<digest12>`` — an Ollama model pinned by its local digest (OLL-003).
_DIGEST_MODEL = re.compile(r"^(?P<name>.+)@(?P<digest>[0-9a-f]{12})$")
#: Suffix of an :func:`enforce_requirements` model meaning "any digest of this name".
ANY_DIGEST = "@*"


def split_digest(model: str) -> tuple[str, str] | None:
    """``(name, digest12)`` for a digest-pinned model, ``None`` for a plain name."""
    match = _DIGEST_MODEL.match(model)
    return (match["name"], match["digest"]) if match else None


@dataclass(frozen=True)
class CalibrationRecord:
    """One fitted calibration for a ``(decision_id, model)`` pair."""

    decision_id: str
    model: str
    method: Literal["temperature", "isotonic"]
    params: Mapping[str, Any]
    tau: float
    target_precision: float
    ece_before: float
    ece_after: float
    n_by_language: Mapping[str, int] = field(default_factory=dict)
    precision_by_language: Mapping[str, float] = field(default_factory=dict)
    created_at: str = ""
    version: str = ""

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe mapping (the on-disk format)."""
        data = asdict(self)
        data["params"] = dict(self.params)
        data["n_by_language"] = dict(self.n_by_language)
        data["precision_by_language"] = dict(self.precision_by_language)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CalibrationRecord:
        """Rebuild from :meth:`to_dict` output; raises ``KeyError``/``ValueError`` if malformed."""
        raw_method = str(data["method"])
        if raw_method not in {"temperature", "isotonic"}:
            raise ValueError(f"unknown calibration method {raw_method!r}")
        method: Literal["temperature", "isotonic"] = (
            "temperature" if raw_method == "temperature" else "isotonic"
        )
        numbers = [float(data[k]) for k in ("tau", "target_precision", "ece_before", "ece_after")]
        if not all(math.isfinite(n) for n in numbers):
            raise ValueError("non-finite calibration metric")
        params = dict(data.get("params") or {})
        if method == "temperature" and not float(params.get("temperature", 1.0)) > 0.0:
            raise ValueError("temperature must be > 0")
        return cls(
            decision_id=str(data["decision_id"]),
            model=str(data["model"]),
            method=method,
            params=params,
            tau=float(data["tau"]),
            target_precision=float(data["target_precision"]),
            ece_before=float(data["ece_before"]),
            ece_after=float(data["ece_after"]),
            n_by_language={k: int(v) for k, v in (data.get("n_by_language") or {}).items()},
            precision_by_language={
                k: float(v) for k, v in (data.get("precision_by_language") or {}).items()
            },
            created_at=str(data.get("created_at", "")),
            version=str(data.get("version", "")),
        )


def _segment(text: str) -> str:
    """File-name-safe key segment: path separators can never leave the directory (L1)."""
    return text.replace("/", "__").replace("\\", "__")


class CalibrationStore:
    """Directory of calibration records keyed by ``<decision_id>@<model>.json``.

    ``model`` is a plain pinned name (Laya / Jev) or ``<name>@<digest12>`` (Ollama).
    """

    def __init__(self, directory: str | Path) -> None:
        self._dir = Path(directory)
        self._cache: dict[tuple[str, str], CalibrationRecord | None] = {}
        self._stale: dict[tuple[str, str], bool] = {}

    @property
    def directory(self) -> Path:
        """Directory holding the calibration records."""
        return self._dir

    def _path(self, decision_id: str, model: str) -> Path:
        return self._dir / f"{_segment(decision_id)}@{_segment(model)}.json"

    def get(self, decision_id: str, model: str) -> CalibrationRecord | None:
        """Return the record, or ``None`` when absent, unreadable or for another model."""
        key = (decision_id, model)
        if key in self._cache:
            return self._cache[key]
        record: CalibrationRecord | None = None
        path = self._path(decision_id, model)
        if path.is_file():
            try:
                loaded = CalibrationRecord.from_dict(json.loads(path.read_text("utf-8")))
            except (OSError, ValueError, KeyError, TypeError):
                logger.warning("decision_calibration_unreadable", path=str(path))
            else:
                if loaded.decision_id == decision_id and loaded.model == model:
                    record = loaded
        self._cache[key] = record
        return record

    def records_for_name(self, decision_id: str, name: str) -> list[CalibrationRecord]:
        """Valid records for any digest of Ollama model ``name``, newest first.

        Only ``<decision_id>@<name>@<digest12>.json`` files whose content matches
        their key (as :meth:`get` checks) count; unreadable files are skipped.
        """
        prefix = f"{_segment(decision_id)}@{_segment(name)}@"
        out: list[CalibrationRecord] = []
        try:
            paths = sorted(self._dir.glob("*.json")) if self._dir.is_dir() else []
        except OSError:
            paths = []
        for path in paths:
            stem = path.name[: -len(".json")]
            if not stem.startswith(prefix):
                continue
            model = f"{name}@{stem[len(prefix) :]}"
            parsed = split_digest(model)
            if parsed is None or parsed[0] != name:
                continue
            record = self.get(decision_id, model)
            if record is not None:
                out.append(record)
        out.sort(key=lambda r: (r.created_at, r.model), reverse=True)
        return out

    def stale(self, decision_id: str, model: str) -> bool:
        """True when a digest-pinned ``model`` lost its calibration to a re-pull (OLL-003).

        Stale ⇔ ``model`` is ``<name>@<digest12>``, there is no valid record for
        exactly that key, but one exists for the same ``decision_id`` and ``name``
        under another digest. Plain model names (Laya / Jev) are never stale.
        """
        key = (decision_id, model)
        if key not in self._stale:
            parsed = split_digest(model)
            self._stale[key] = (
                parsed is not None
                and self.get(decision_id, model) is None
                and bool(self.records_for_name(decision_id, parsed[0]))
            )
        return self._stale[key]

    def save(self, record: CalibrationRecord) -> Path:
        """Write ``record`` to ``<dir>/<decision_id>@<model>.json`` and return the path."""
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._path(record.decision_id, record.model)
        path.write_text(json.dumps(record.to_dict(), indent=2, sort_keys=True) + "\n", "utf-8")
        self._cache[(record.decision_id, record.model)] = record
        self._stale.clear()
        return path

    def validate_for_enforce(
        self,
        decision_id: str,
        model: str,
        languages: Sequence[str] = (),
        *,
        max_ece: float = 0.05,
        min_examples: int = 300,
    ) -> None:
        """Raise ``DecisionCalibrationError`` unless the record may enforce ``languages``."""
        record = self.get(decision_id, model)
        if record is None:
            raise DecisionCalibrationError(
                f"no calibration for decision {decision_id!r} on model {model!r} "
                f"in {self._dir} — enforce refused (CAL-002)"
            )
        if not record.ece_after <= max_ece:
            raise DecisionCalibrationError(
                f"calibration {decision_id!r}@{model!r} has ECE {record.ece_after:.4f} "
                f"> {max_ece} — enforce refused (CAL-002)"
            )
        missing = [
            lang
            for lang in languages
            if not language_enforceable(record, lang, min_examples=min_examples)
        ]
        if missing:
            raise DecisionCalibrationError(
                f"calibration {decision_id!r}@{model!r} lacks a valid slice for {missing} (CAL-003)"
            )

    def validate_for_enforce_any_digest(
        self,
        decision_id: str,
        name: str,
        languages: Sequence[str] = (),
        *,
        max_ece: float = 0.05,
        min_examples: int = 300,
    ) -> None:
        """CAL-002 for Ollama: some ``<decision_id>@<name>@<digest12>`` record must pass.

        The digest is unknown at settings load (it comes from the runtime
        preflight), so any digest of ``name`` satisfies the startup gate; a digest
        mismatch at request time is handled as *stale* (shadow-only).
        """
        records = self.records_for_name(decision_id, name)
        if not records:
            raise DecisionCalibrationError(
                f"no calibration for decision {decision_id!r} on any digest of Ollama model "
                f"{name!r} in {self._dir} — enforce refused (CAL-002)"
            )
        errors: list[DecisionCalibrationError] = []
        for record in records:
            try:
                self.validate_for_enforce(
                    decision_id,
                    record.model,
                    languages,
                    max_ece=max_ece,
                    min_examples=min_examples,
                )
            except DecisionCalibrationError as exc:
                errors.append(exc)
            else:
                return
        raise errors[0]  # newest record's reason (records is non-empty)


def language_enforceable(record: CalibrationRecord, language: str, *, min_examples: int) -> bool:
    """CAL-003: a language slice may enforce only with enough examples at target precision."""
    n = record.n_by_language.get(language, 0)
    precision = record.precision_by_language.get(language, 0.0)
    return n >= min_examples and precision >= record.target_precision


def enforce_requirements(settings: Settings) -> list[tuple[str, str, str]]:
    """``(integration, decision_id, model)`` triples that need a record to enforce.

    Laya / Jev models are exact keys. Ollama models are ``<name>@*`` (see
    :data:`ANY_DIGEST`): any ``<name>@<digest12>`` record may satisfy them.
    """
    out: list[tuple[str, str, str]] = []
    for integration in INTEGRATION_FLAGS:
        if configured_mode(settings, integration) != "enforce":
            continue
        for backend in settings.decision_backend_order:
            suffix = ANY_DIGEST if backend == "ollama" else ""
            for model in backend_models(settings, backend):
                out.extend(
                    (integration, decision_id, f"{model}{suffix}")
                    for decision_id in INTEGRATION_DECISION_IDS[integration]
                )
    return out


def validate_enforce_calibration(settings: Settings) -> None:
    """CAL-002 startup gate: every enforced ``(decision_id, model)`` needs a valid record.

    Language slices are not required here: a language failing CAL-003 runs in
    shadow at request time while the others may enforce.
    """
    requirements = enforce_requirements(settings)
    if not requirements:
        return
    store = CalibrationStore(settings.decision_calibration_dir)
    for _integration, decision_id, model in requirements:
        if model.endswith(ANY_DIGEST):
            store.validate_for_enforce_any_digest(
                decision_id, model[: -len(ANY_DIGEST)], max_ece=settings.decision_max_ece
            )
        else:
            store.validate_for_enforce(decision_id, model, max_ece=settings.decision_max_ece)


def _interp(x: float, xs: Sequence[float], ys: Sequence[float]) -> float:
    """Piecewise-linear, clamped interpolation (isotonic map evaluation)."""
    if not xs:
        return x
    if x <= xs[0]:
        return float(ys[0])
    if x >= xs[-1]:
        return float(ys[-1])
    for i in range(1, len(xs)):
        if x <= xs[i]:
            x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
            return float(y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0))
    return float(ys[-1])  # pragma: no cover - loop always returns


def calibrate_probabilities(
    probabilities: Mapping[str, float], record: CalibrationRecord
) -> dict[str, float]:
    """Apply ``record`` to a raw distribution (temperature or isotonic top-label map)."""
    probs = {k: max(float(v), 0.0) for k, v in probabilities.items()}
    if not probs:
        return probs
    if record.method == "temperature":
        t = float(record.params.get("temperature", 1.0)) or 1.0
        eps = 1e-12
        scaled = {k: math.exp(math.log(max(v, eps)) / t) for k, v in probs.items()}
        total = sum(scaled.values())
        return {k: v / total for k, v in scaled.items()}
    xs = [float(x) for x in record.params.get("x", [])]
    ys = [float(y) for y in record.params.get("y", [])]
    top = max(probs, key=lambda k: probs[k])
    p_top = probs[top]
    new_top = min(max(_interp(p_top, xs, ys), 0.0), 1.0)
    rest = 1.0 - p_top
    out: dict[str, float] = {}
    for k, v in probs.items():
        if k == top:
            out[k] = new_top
        else:
            out[k] = (1.0 - new_top) * (v / rest if rest > 0 else 1.0 / (len(probs) - 1))
    return out


def apply_calibration(d: Decision, record: CalibrationRecord, question: QuestionSpec) -> Decision:
    """Return ``d`` with calibrated probabilities and ``calibration_version`` set.

    Abstentions pass through unchanged. ``spread_confidence`` (vendor field) is kept.
    """
    if d.abstained:
        return d
    calibrated = calibrate_probabilities(d.probabilities, record)
    out = Decision.from_probabilities(
        question,
        calibrated,
        backend=d.backend,
        model=d.model,
        spread_confidence=d.spread_confidence,
        calibration_version=record.version or record.created_at or "unversioned",
        latency_ms=d.latency_ms,
    )
    return replace(out, outcome=d.outcome, escalated_from=d.escalated_from, reason=d.reason)


# ── Labeled sets and fitting (CAL-001) ──────────────────────────────────────────


@dataclass(frozen=True)
class LabeledExample:
    """One human-labelled decision (JSONL row)."""

    state: Mapping[str, str]
    question_id: str
    label: str
    language: str = "en"


def load_labeled_set(path: str | Path) -> list[LabeledExample]:
    """Read a JSONL labeled set: ``{state, question_id, label, language?}`` per line."""
    out: list[LabeledExample] = []
    for lineno, line in enumerate(Path(path).read_text("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            out.append(
                LabeledExample(
                    state={str(k): str(v) for k, v in dict(row["state"]).items()},
                    question_id=str(row["question_id"]),
                    label=str(row["label"]),
                    language=str(row.get("language") or "en"),
                )
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"{path}: malformed labeled example on line {lineno}") from exc
    return out


def is_correct(d: Decision, label: str) -> bool:
    """True when a non-abstained decision's value matches ``label`` (Noul: true/false)."""
    if d.abstained or d.value is None:
        return False
    value = ("true" if d.value else "false") if isinstance(d.value, bool) else str(d.value)
    return value.lower() == label.strip().lower()


def _pairs(
    preds: Sequence[Decision], examples: Sequence[LabeledExample]
) -> list[tuple[Decision, LabeledExample]]:
    if len(preds) != len(examples):
        raise ValueError(f"{len(preds)} predictions for {len(examples)} labeled examples")
    return [(d, e) for d, e in zip(preds, examples, strict=True) if not d.abstained]


def expected_calibration_error(
    preds: Sequence[Decision], examples: Sequence[LabeledExample], *, bins: int = 10
) -> float:
    """Top-label ECE over equal-width confidence bins (abstentions excluded)."""
    pairs = _pairs(preds, examples)
    if not pairs:
        return 0.0
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(bins)]
    for d, e in pairs:
        index = min(int(d.answer_confidence * bins), bins - 1)
        buckets[index].append((d.answer_confidence, is_correct(d, e.label)))
    total = len(pairs)
    ece = 0.0
    for bucket in buckets:
        if bucket:
            conf = sum(c for c, _ in bucket) / len(bucket)
            acc = sum(1 for _, ok in bucket if ok) / len(bucket)
            ece += len(bucket) / total * abs(conf - acc)
    return ece


def _label_key(d: Decision, label: str) -> str:
    return label.strip().lower() if isinstance(d.value, bool) else label


def _nll(pairs: list[tuple[Decision, LabeledExample]], temperature: float) -> float:
    eps = 1e-12
    record = CalibrationRecord(
        decision_id="",
        model="",
        method="temperature",
        params={"temperature": temperature},
        tau=0.0,
        target_precision=0.0,
        ece_before=0.0,
        ece_after=0.0,
    )
    loss = 0.0
    for d, e in pairs:
        probs = calibrate_probabilities(d.probabilities, record)
        loss -= math.log(max(probs.get(_label_key(d, e.label), 0.0), eps))
    return loss


def _fit_temperature(pairs: list[tuple[Decision, LabeledExample]]) -> float:
    grid = [
        math.exp(math.log(0.05) + i * (math.log(50.0) - math.log(0.05)) / 399) for i in range(400)
    ]
    best = min(grid, key=lambda t: _nll(pairs, t))
    lo, hi = best / 1.05, best * 1.05
    for _ in range(40):  # golden-section refinement around the grid optimum
        m1, m2 = lo + (hi - lo) * 0.382, lo + (hi - lo) * 0.618
        if _nll(pairs, m1) < _nll(pairs, m2):
            hi = m2
        else:
            lo = m1
    return (lo + hi) / 2


def _fit_isotonic(pairs: list[tuple[Decision, LabeledExample]]) -> dict[str, list[float]]:
    """Pool-adjacent-violators over (top-label confidence, correct)."""
    tied: dict[float, list[float]] = {}  # identical confidences pool first
    for d, e in pairs:
        acc = tied.setdefault(d.answer_confidence, [0.0, 0.0])
        acc[0] += 1.0 if is_correct(d, e.label) else 0.0
        acc[1] += 1.0
    blocks: list[list[float]] = []  # [sum_x, sum_y, n]
    for x in sorted(tied):
        sum_y, n = tied[x]
        blocks.append([x * n, sum_y, n])
        while len(blocks) > 1 and blocks[-2][1] / blocks[-2][2] > blocks[-1][1] / blocks[-1][2]:
            sx, sy, n = blocks.pop()
            blocks[-1][0] += sx
            blocks[-1][1] += sy
            blocks[-1][2] += n
    return {
        "x": [round(b[0] / b[2], 6) for b in blocks],
        "y": [round(b[1] / b[2], 6) for b in blocks],
    }


def fit_calibration(
    preds: Sequence[Decision],
    examples: Sequence[LabeledExample],
    *,
    method: Literal["temperature", "isotonic"],
) -> dict[str, Any]:
    """Fit calibration params: Laya → temperature scaling, Jev → isotonic map."""
    pairs = _pairs(preds, examples)
    if method == "temperature":
        return {"temperature": round(_fit_temperature(pairs), 6) if pairs else 1.0}
    if method == "isotonic":
        return _fit_isotonic(pairs) if pairs else {"x": [], "y": []}
    raise ValueError(f"unknown calibration method {method!r}")


def fit_thresholds(
    preds: Sequence[Decision],
    examples: Sequence[LabeledExample],
    *,
    target_precision: float,
    floor: float,
) -> float:
    """Smallest τ ≥ ``floor`` whose accepted subset reaches ``target_precision`` (else 1.0)."""
    pairs = _pairs(preds, examples)
    candidates = sorted({d.answer_confidence for d, _ in pairs if d.answer_confidence >= floor})
    for tau in candidates:
        selected = [is_correct(d, e.label) for d, e in pairs if d.answer_confidence >= tau]
        if selected and sum(selected) / len(selected) >= target_precision:
            return tau
    return 1.0


def build_calibration_record(
    preds: Sequence[Decision],
    examples: Sequence[LabeledExample],
    *,
    decision_id: str,
    model: str,
    method: Literal["temperature", "isotonic"],
    target_precision: float,
    floor: float,
    version: str,
    questions: Mapping[str, QuestionSpec] | None = None,
) -> CalibrationRecord:
    """Fit, evaluate (ECE before/after, per-language precision at τ) and package a record."""
    params = fit_calibration(preds, examples, method=method)
    draft = CalibrationRecord(
        decision_id=decision_id,
        model=model,
        method=method,
        params=params,
        tau=floor,
        target_precision=target_precision,
        ece_before=0.0,
        ece_after=0.0,
        version=version,
    )
    calibrated = [_recalibrated(d, draft, questions) if not d.abstained else d for d in preds]
    tau = fit_thresholds(calibrated, examples, target_precision=target_precision, floor=floor)
    n_by_language: dict[str, int] = {}
    precision_by_language: dict[str, float] = {}
    for language in sorted({e.language for e in examples}):
        subset = [
            (d, e) for d, e in zip(calibrated, examples, strict=True) if e.language == language
        ]
        n_by_language[language] = len(subset)
        accepted = [is_correct(d, e.label) for d, e in subset if d.answer_confidence >= tau]
        precision_by_language[language] = sum(accepted) / len(accepted) if accepted else 0.0
    from datetime import UTC, datetime

    return replace(
        draft,
        tau=tau,
        ece_before=expected_calibration_error(preds, examples),
        ece_after=expected_calibration_error(calibrated, examples),
        n_by_language=n_by_language,
        precision_by_language=precision_by_language,
        created_at=datetime.now(UTC).isoformat(),
    )


def _recalibrated(
    d: Decision, record: CalibrationRecord, questions: Mapping[str, QuestionSpec] | None
) -> Decision:
    """Calibrate without the original spec: rebuild a minimal one from the distribution."""
    if questions and d.question_id in questions:
        return apply_calibration(d, record, questions[d.question_id])
    from prismal.decisions.types import DecisionKind, OptionSpec
    from prismal.decisions.types import QuestionSpec as Spec

    labels = tuple(d.probabilities)
    if d.kind is DecisionKind.NOUL:
        q = Spec(id=d.question_id, kind=d.kind, instructions="")
    elif d.kind is DecisionKind.SCORE:
        q = Spec(id=d.question_id, kind=d.kind, instructions="", levels=labels)
    else:
        q = Spec(
            id=d.question_id,
            kind=d.kind,
            instructions="",
            options=tuple(OptionSpec(lab, lab) for lab in labels),
        )
    return apply_calibration(d, record, q)
