"""Calibration fitting and store (Phase DEC — SPEC-DEC-CAL-001/002/003, DEC3-01..03)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prismal.core.exceptions import DecisionCalibrationError
from prismal.decisions.calibration import (
    CalibrationRecord,
    CalibrationStore,
    LabeledExample,
    apply_calibration,
    build_calibration_record,
    expected_calibration_error,
    fit_calibration,
    fit_thresholds,
    is_correct,
    language_enforceable,
    load_labeled_set,
)
from prismal.decisions.types import Decision, DecisionKind, OptionSpec, QuestionSpec

Q = QuestionSpec(
    id="route.supervisor",
    kind=DecisionKind.CHOICE,
    instructions="Pick one.",
    options=(OptionSpec("a", "A"), OptionSpec("b", "B")),
)


def _overconfident(n: int = 1000, correct_ratio: float = 0.7, lang: str = "en"):
    """Model always says 'a' with p=0.99; only ``correct_ratio`` of labels are 'a'."""
    preds, examples = [], []
    n_correct = int(n * correct_ratio)
    for i in range(n):
        preds.append(
            Decision.from_probabilities(Q, {"a": 0.99, "b": 0.01}, backend="laya", model="m")
        )
        label = "a" if i < n_correct else "b"
        examples.append(
            LabeledExample(state={"u": str(i)}, question_id=Q.id, label=label, language=lang)
        )
    return preds, examples


def test_load_labeled_set(tmp_path: Path) -> None:
    path = tmp_path / "set.jsonl"
    rows = [
        {"state": {"u": "hola"}, "question_id": Q.id, "label": "a", "language": "es"},
        {"state": {"u": "hi"}, "question_id": Q.id, "label": "b"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n\n")
    out = load_labeled_set(path)
    assert out[0] == LabeledExample(state={"u": "hola"}, question_id=Q.id, label="a", language="es")
    assert out[1].language == "en"


def test_load_labeled_set_rejects_malformed(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"state": {}, "label": "a"}\n')
    with pytest.raises(ValueError, match="line 1"):
        load_labeled_set(path)


def test_is_correct_noul_and_choice() -> None:
    noul = QuestionSpec(id="n", kind=DecisionKind.NOUL, instructions="Relevant?")
    d = Decision.from_probabilities(noul, {"true": 0.8}, backend="x", model="m")
    assert is_correct(d, "true")
    assert is_correct(d, "True")
    assert not is_correct(d, "false")
    assert not is_correct(Decision.abstain(noul, backend="x", model="m", reason="timeout"), "true")


def test_ece_of_overconfident_model_is_large() -> None:
    preds, examples = _overconfident()
    assert expected_calibration_error(preds, examples) == pytest.approx(0.29, abs=0.01)


def test_temperature_scaling_reduces_ece() -> None:
    preds, examples = _overconfident()
    params = fit_calibration(preds, examples, method="temperature")
    assert params["temperature"] > 1.0
    record = CalibrationRecord(
        decision_id=Q.id,
        model="m",
        method="temperature",
        params=params,
        tau=0.9,
        target_precision=0.95,
        ece_before=0.0,
        ece_after=0.0,
    )
    calibrated = [apply_calibration(d, record, Q) for d in preds]
    assert expected_calibration_error(calibrated, examples) <= 0.05
    assert calibrated[0].answer_confidence == pytest.approx(0.7, abs=0.03)


def test_isotonic_reduces_ece() -> None:
    preds, examples = _overconfident()
    params = fit_calibration(preds, examples, method="isotonic")
    assert set(params) == {"x", "y"}
    assert params["y"] == sorted(params["y"])  # monotone
    record = CalibrationRecord(
        decision_id=Q.id,
        model="m",
        method="isotonic",
        params=params,
        tau=0.9,
        target_precision=0.95,
        ece_before=0.0,
        ece_after=0.0,
    )
    calibrated = [apply_calibration(d, record, Q) for d in preds]
    assert expected_calibration_error(calibrated, examples) <= 0.05


def test_fit_rejects_mismatched_lengths_and_unknown_method() -> None:
    preds, examples = _overconfident(10)
    with pytest.raises(ValueError):
        fit_calibration(preds, examples[:5], method="temperature")
    with pytest.raises(ValueError):
        fit_calibration(preds, examples, method="platt")  # type: ignore[arg-type]


def test_fit_thresholds_smallest_tau_reaching_precision() -> None:
    preds, examples = [], []
    # confidence 0.95 → always right; 0.8 → right half the time
    for i in range(100):
        preds.append(Decision.from_probabilities(Q, {"a": 0.95, "b": 0.05}, backend="x", model="m"))
        examples.append(LabeledExample({}, Q.id, "a", "en"))
        preds.append(Decision.from_probabilities(Q, {"a": 0.8, "b": 0.2}, backend="x", model="m"))
        examples.append(LabeledExample({}, Q.id, "a" if i % 2 else "b", "en"))
    assert fit_thresholds(preds, examples, target_precision=0.97, floor=0.5) == pytest.approx(0.95)
    # clamped to the tier default
    assert fit_thresholds(preds, examples, target_precision=0.5, floor=0.9) == pytest.approx(0.95)
    assert fit_thresholds(preds, examples, target_precision=0.5, floor=0.6) == pytest.approx(0.8)


def test_fit_thresholds_unreachable_returns_one() -> None:
    preds, examples = _overconfident(100, correct_ratio=0.5)
    assert fit_thresholds(preds, examples, target_precision=0.99, floor=0.85) == 1.0


def test_build_record_with_spanish_slice() -> None:
    en_p, en_e = _overconfident(600, correct_ratio=0.98, lang="en")
    es_p, es_e = _overconfident(400, correct_ratio=0.98, lang="es")
    record = build_calibration_record(
        en_p + es_p,
        en_e + es_e,
        decision_id=Q.id,
        model="m",
        method="temperature",
        target_precision=0.95,
        floor=0.85,
        version="v1",
    )
    assert record.n_by_language == {"en": 600, "es": 400}
    assert record.precision_by_language["es"] == pytest.approx(0.98, abs=0.01)
    assert record.ece_after <= record.ece_before
    assert language_enforceable(record, "es", min_examples=300)
    assert not language_enforceable(record, "es", min_examples=500)
    assert not language_enforceable(record, "fr", min_examples=1)


class TestStore:
    def _record(self, **over: object) -> CalibrationRecord:
        base: dict[str, object] = {
            "decision_id": Q.id,
            "model": "laya-multilingual",
            "method": "temperature",
            "params": {"temperature": 2.0},
            "tau": 0.9,
            "target_precision": 0.95,
            "ece_before": 0.4,
            "ece_after": 0.03,
            "n_by_language": {"en": 500, "es": 400},
            "precision_by_language": {"en": 0.97, "es": 0.96},
            "version": "v1",
        }
        base.update(over)
        return CalibrationRecord.from_dict(base)

    def test_round_trip(self, tmp_path: Path) -> None:
        store = CalibrationStore(tmp_path)
        path = store.save(self._record())
        assert path.name == "route.supervisor@laya-multilingual.json"
        assert CalibrationStore(tmp_path).get(Q.id, "laya-multilingual") == self._record()

    def test_model_change_invalidates(self, tmp_path: Path) -> None:
        CalibrationStore(tmp_path).save(self._record())
        assert CalibrationStore(tmp_path).get(Q.id, "laya-multilingual@v2") is None

    def test_mislabelled_file_ignored(self, tmp_path: Path) -> None:
        (tmp_path / f"{Q.id}@other.json").write_text(json.dumps(self._record().to_dict()))
        assert CalibrationStore(tmp_path).get(Q.id, "other") is None

    def test_unreadable_file_ignored(self, tmp_path: Path) -> None:
        (tmp_path / f"{Q.id}@laya-multilingual.json").write_text("{not json")
        assert CalibrationStore(tmp_path).get(Q.id, "laya-multilingual") is None

    def test_validate_for_enforce(self, tmp_path: Path) -> None:
        store = CalibrationStore(tmp_path)
        with pytest.raises(DecisionCalibrationError, match="no calibration"):
            store.validate_for_enforce(Q.id, "laya-multilingual", ["en"])
        store.save(self._record())
        store.validate_for_enforce(Q.id, "laya-multilingual", ["en", "es"])
        with pytest.raises(DecisionCalibrationError, match="CAL-003"):
            store.validate_for_enforce(Q.id, "laya-multilingual", ["es"], min_examples=1000)

    def test_validate_rejects_high_ece(self, tmp_path: Path) -> None:
        store = CalibrationStore(tmp_path)
        store.save(self._record(ece_after=0.2))
        with pytest.raises(DecisionCalibrationError, match="ECE"):
            store.validate_for_enforce(Q.id, "laya-multilingual", [])
