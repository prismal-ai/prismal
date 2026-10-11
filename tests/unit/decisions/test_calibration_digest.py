"""Calibration keyed by the local Ollama model digest (DEC delta — SPEC-DEC-OLL-003, CAL-002).

Ollama sub-tier backends expose ``model = "<name>@<digest12>"``; the store keys
records as ``<decision_id>@<name>@<digest12>.json``. A digest change leaves the
decision uncalibrated (shadow-only: it escalates) and is reported as *stale*.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from prismal.core.config import Settings
from prismal.core.exceptions import DecisionCalibrationError
from prismal.decisions import cascade as cascade_mod
from prismal.decisions.adapters.fake import FakeDecisionAdapter
from prismal.decisions.calibration import (
    CalibrationRecord,
    CalibrationStore,
    enforce_requirements,
    validate_enforce_calibration,
)
from prismal.decisions.cascade import DecisionCascade
from prismal.decisions.catalog import backend_model, backend_models
from prismal.decisions.state import DecisionState, DecisionStateBuilder
from prismal.decisions.types import (
    Decision,
    DecisionKind,
    GatePolicy,
    OptionSpec,
    Outcome,
    QuestionSpec,
    RiskTier,
)

QID = "route.supervisor"
OLD = "tev1:4b@000000000000"
NEW = "tev1:4b@9b5bb969e46c"
ROUTE = QuestionSpec(
    id=QID,
    kind=DecisionKind.CHOICE,
    instructions="Pick the best agent.",
    options=(OptionSpec("coder", "Writes code"), OptionSpec("researcher", "Searches")),
    risk_tier=RiskTier.MEDIUM,
)
POLICY = GatePolicy(timeout_ms=500)


def _record(model: str, decision_id: str = QID, **over: Any) -> CalibrationRecord:
    data: dict[str, Any] = {
        "decision_id": decision_id,
        "model": model,
        "method": "temperature",
        "params": {"temperature": 1.0},
        "tau": 0.9,
        "target_precision": 0.95,
        "ece_before": 0.3,
        "ece_after": 0.02,
        "n_by_language": {"en": 500, "es": 500},
        "precision_by_language": {"en": 0.99, "es": 0.99},
        "created_at": "2026-10-05T00:00:00+00:00",
        "version": "v1",
    }
    data.update(over)
    return CalibrationRecord.from_dict(data)


def _save(directory: Path, model: str, decision_id: str = QID, **over: Any) -> None:
    CalibrationStore(directory).save(_record(model, decision_id, **over))


# ── stale detection (OLL-003) ───────────────────────────────────────────────


class TestStale:
    def test_other_digest_present_is_stale(self, tmp_path: Path) -> None:
        _save(tmp_path, OLD)
        store = CalibrationStore(tmp_path)
        assert store.get(QID, NEW) is None
        assert store.stale(QID, NEW) is True

    def test_exact_digest_present_is_not_stale(self, tmp_path: Path) -> None:
        _save(tmp_path, OLD)
        _save(tmp_path, NEW)
        assert CalibrationStore(tmp_path).stale(QID, NEW) is False

    def test_no_record_at_all_is_not_stale(self, tmp_path: Path) -> None:
        assert CalibrationStore(tmp_path).stale(QID, NEW) is False

    def test_plain_model_names_are_never_stale(self, tmp_path: Path) -> None:
        _save(tmp_path, "laya-multilingual@000000000000")
        store = CalibrationStore(tmp_path)
        assert store.stale(QID, "laya-multilingual") is False
        assert store.stale(QID, "jev-1.13.0") is False

    def test_other_model_name_or_decision_is_not_stale(self, tmp_path: Path) -> None:
        _save(tmp_path, "nimble:9b-q4_K_M@572f1f4c801d")
        _save(tmp_path, OLD, decision_id="rag.chunk_relevance")
        assert CalibrationStore(tmp_path).stale(QID, NEW) is False

    def test_malformed_files_are_ignored(self, tmp_path: Path) -> None:
        (tmp_path / f"{QID}@{OLD}.json").write_text("{not json")
        (tmp_path / f"{QID}@tev1:4b@nothex000000.json").write_text(
            json.dumps(_record("tev1:4b@nothex000000").to_dict())
        )
        # File name and content disagree (mislabelled) ⇒ ignored too.
        (tmp_path / f"{QID}@tev1:4b@111111111111.json").write_text(
            json.dumps(_record("tev1:4b@222222222222").to_dict())
        )
        assert CalibrationStore(tmp_path).stale(QID, NEW) is False

    def test_latest_for_name_picks_newest_valid_digest(self, tmp_path: Path) -> None:
        _save(tmp_path, OLD, created_at="2026-01-01T00:00:00+00:00")
        _save(tmp_path, NEW, created_at="2026-10-01T00:00:00+00:00")
        _save(tmp_path, "tev1:4b-other@333333333333", created_at="2027-01-01T00:00:00+00:00")
        records = CalibrationStore(tmp_path).records_for_name(QID, "tev1:4b")
        assert [r.model for r in records] == [NEW, OLD]


# ── cascade: stale ⇒ uncalibrated ⇒ escalated + metric + one warning ────────


def _state() -> DecisionState:
    return DecisionStateBuilder().add_untrusted("user", "write a sort", source="user").build()


def _confident() -> Decision:
    return Decision.from_probabilities(
        ROUTE, {"coder": 0.99, "researcher": 0.01}, backend="x", model="x"
    )


async def test_cascade_escalates_on_stale_digest_and_reports_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save(tmp_path, OLD)
    calls: list[tuple[str, dict[str, Any] | None]] = []
    monkeypatch.setattr(cascade_mod, "inc", lambda m, a=None: calls.append((m, a)))
    log = MagicMock()
    monkeypatch.setattr(cascade_mod, "logger", log)
    backend = FakeDecisionAdapter({QID: _confident()}, name="ollama:fast", model=NEW)
    cascade = DecisionCascade([backend], calibration=CalibrationStore(tmp_path))

    for _ in range(2):
        out = await cascade.decide(_state(), {QID: ROUTE}, policy=POLICY, integration="route")
        d = out[QID]
        assert d.outcome is Outcome.ESCALATED
        assert d.reason == "uncalibrated"

    stale = [a for m, a in calls if m == "decision_calibration_stale"]
    assert stale == [{"decision_id": QID, "backend": "ollama:fast"}] * 2
    warned = [c for c in log.warning.call_args_list if c.args[0] == "decision_calibration_stale"]
    assert len(warned) == 1


async def test_cascade_accepts_with_exact_digest_and_emits_no_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _save(tmp_path, NEW)
    calls: list[str] = []
    monkeypatch.setattr(cascade_mod, "inc", lambda m, a=None: calls.append(m))
    backend = FakeDecisionAdapter({QID: _confident()}, name="ollama:fast", model=NEW)
    cascade = DecisionCascade([backend], calibration=CalibrationStore(tmp_path))
    out = await cascade.decide(_state(), {QID: ROUTE}, policy=POLICY, integration="route")
    assert out[QID].outcome is Outcome.ACCEPTED
    assert "decision_calibration_stale" not in calls


async def test_cascade_uncalibrated_plain_model_emits_no_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cascade_mod, "inc", lambda m, a=None: calls.append(m))
    backend = FakeDecisionAdapter({QID: _confident()}, name="laya", model="laya-multilingual")
    cascade = DecisionCascade([backend], calibration=CalibrationStore(tmp_path))
    out = await cascade.decide(_state(), {QID: ROUTE}, policy=POLICY, integration="route")
    assert out[QID].outcome is Outcome.ESCALATED
    assert "decision_calibration_stale" not in calls


# ── catalog: backend_models ─────────────────────────────────────────────────


def test_backend_models_per_backend() -> None:
    s = Settings()
    assert backend_models(s, "ollama") == ("tev1:4b", "nimble:9b-q4_K_M")
    assert backend_models(s, "laya") == ("laya-multilingual",)
    assert backend_models(s, "jev") == ("jev-1.13.0",)
    assert backend_models(s, "rule") == ()
    assert backend_models(s, "llm") == ()
    assert backend_model(s, "jev") == "jev-1.13.0"
    assert backend_model(s, "ollama") is None


def test_backend_models_ollama_dedupes_collapsed_tiers() -> None:
    s = Settings(
        decision_ollama_models={"fast": "nimble:9b-q4_K_M", "accurate": "nimble:9b-q4_K_M"}
    )
    assert backend_models(s, "ollama") == ("nimble:9b-q4_K_M",)
    assert backend_models(Settings(decision_ollama_models={"accurate": "n:1"}), "ollama") == (
        "n:1",
    )


# ── enforce at startup (CAL-002) ────────────────────────────────────────────


def _enforce_settings(tmp_path: Path, **over: Any) -> Settings:
    kwargs: dict[str, Any] = {
        "decisions_enabled": True,
        "decision_routing_enabled": True,
        "decision_modes": {"route": "enforce"},
        "decision_backend_order": ["rule", "ollama", "llm"],
        "decision_ollama_models": {"fast": "tev1:4b"},
        "decision_calibration_dir": str(tmp_path),
    }
    kwargs.update(over)
    return Settings(**kwargs)


def test_enforce_starts_with_a_record_for_any_digest(tmp_path: Path) -> None:
    _save(tmp_path, OLD)
    s = _enforce_settings(tmp_path)
    assert s.decision_modes["route"] == "enforce"
    validate_enforce_calibration(s)


def test_enforce_refuses_without_any_digest_record(tmp_path: Path) -> None:
    _save(tmp_path, "nimble:9b-q4_K_M@572f1f4c801d")
    with pytest.raises(ValidationError, match=r"tev1:4b"):
        _enforce_settings(tmp_path)


def test_enforce_refuses_when_only_digest_record_has_high_ece(tmp_path: Path) -> None:
    _save(tmp_path, OLD, ece_after=0.2)
    with pytest.raises(ValidationError, match="ECE"):
        _enforce_settings(tmp_path)


def test_enforce_passes_if_any_digest_record_is_valid(tmp_path: Path) -> None:
    _save(tmp_path, OLD, ece_after=0.2, created_at="2026-10-09T00:00:00+00:00")
    _save(tmp_path, NEW, created_at="2026-01-01T00:00:00+00:00")
    _enforce_settings(tmp_path)


def test_enforce_requires_every_configured_ollama_name(tmp_path: Path) -> None:
    _save(tmp_path, OLD)
    with pytest.raises(ValidationError, match=r"nimble:9b-q4_K_M"):
        _enforce_settings(
            tmp_path, decision_ollama_models={"fast": "tev1:4b", "accurate": "nimble:9b-q4_K_M"}
        )


def test_enforce_requirements_mark_ollama_as_any_digest(tmp_path: Path) -> None:
    s = Settings(decision_calibration_dir=str(tmp_path))
    s = s.model_copy(
        update={
            "decisions_enabled": True,
            "decision_routing_enabled": True,
            "decision_modes": {"route": "enforce"},
            "decision_backend_order": ["rule", "ollama", "jev", "llm"],
            "decision_ollama_models": {"fast": "tev1:4b"},
        }
    )
    assert enforce_requirements(s) == [
        ("route", QID, "tev1:4b@*"),
        ("route", QID, "jev-1.13.0"),
    ]


def test_validate_any_digest_error_type(tmp_path: Path) -> None:
    with pytest.raises(DecisionCalibrationError, match="no calibration"):
        CalibrationStore(tmp_path).validate_for_enforce_any_digest(QID, "tev1:4b")


def test_laya_still_needs_exact_key(tmp_path: Path) -> None:
    _save(tmp_path, "laya-multilingual@000000000000")
    with pytest.raises(ValidationError, match="laya-multilingual"):
        _enforce_settings(tmp_path, decision_backend_order=["rule", "laya", "llm"])


class TestPathSafeModelNames:
    """Review L1: model names with path separators stay inside the calibration directory."""

    NAME = "hf.co/org/m:Q4"

    def test_save_and_get_with_slashes(self, tmp_path: Path) -> None:
        store = CalibrationStore(tmp_path)
        path = store.save(_record(f"{self.NAME}@9b5bb969e46c"))
        assert path.parent == tmp_path
        assert CalibrationStore(tmp_path).get(QID, f"{self.NAME}@9b5bb969e46c") is not None

    def test_dot_dot_cannot_escape_the_directory(self, tmp_path: Path) -> None:
        store = CalibrationStore(tmp_path / "cal")
        path = store.save(_record("../../evil@9b5bb969e46c"))
        assert path.resolve().is_relative_to((tmp_path / "cal").resolve())

    def test_any_digest_and_stale_work_with_slashes(self, tmp_path: Path) -> None:
        _save(tmp_path, f"{self.NAME}@9b5bb969e46c")
        store = CalibrationStore(tmp_path)
        assert [r.model for r in store.records_for_name(QID, self.NAME)] == [
            f"{self.NAME}@9b5bb969e46c"
        ]
        assert store.stale(QID, f"{self.NAME}@aaaaaaaaaaaa") is True

    def test_plain_names_keep_their_file_name(self, tmp_path: Path) -> None:
        path = CalibrationStore(tmp_path).save(_record("tev1:4b@9b5bb969e46c"))
        assert path.name == f"{QID}@tev1:4b@9b5bb969e46c.json"
