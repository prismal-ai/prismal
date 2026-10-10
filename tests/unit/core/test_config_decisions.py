"""Tests for the typed-decision settings (Phase DEC — SPEC-DEC-CFG-001, CAL-002)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from prismal.core.config import Settings

_INTEGRATION_FLAGS = (
    "decision_routing_enabled",
    "decision_eval_judge_enabled",
    "decision_hitl_enabled",
    "decision_tool_proposal_enabled",
    "decision_rag_relevance_enabled",
    "decision_ctx_relevance_enabled",
    "decision_swarm_eval_enabled",
    "decision_safety_prescreen_enabled",
)


def test_decision_defaults_match_spec() -> None:
    s = Settings()
    assert s.decisions_enabled is False
    for flag in _INTEGRATION_FLAGS:
        assert getattr(s, flag) is False, flag
    assert s.decision_backend_order == ["rule", "laya", "jev", "llm"]
    assert s.decision_modes == {}
    assert s.jev_api_key is None
    assert s.jev_model == "jev-1.13.0"
    assert s.jev_transport == "http"
    assert s.laya_mode == "local"
    assert s.laya_checkpoint == "laya-multilingual"
    assert s.laya_base_url is None
    assert s.laya_api_key is None
    assert s.laya_preload is False
    assert s.laya_max_loaded == 2
    assert s.decision_timeout_ms == 800
    assert s.decision_calibration_dir == "config/decision_calibration"
    assert s.decision_languages == ["en", "es"]
    assert s.decision_min_calibration_examples == 300
    assert s.decision_max_ece == 0.05
    assert s.decision_tau_overrides == {}
    assert s.decision_shadow_sample_rate == 1.0
    assert s.decision_eval_audit_rate == 0.05
    assert s.decision_auto_demote is True
    assert s.decision_llm_fallback_model is None


def test_jev_api_key_is_secret() -> None:
    s = Settings(jev_api_key="sk-jev-secret")
    assert s.jev_api_key is not None
    assert "sk-jev-secret" not in repr(s)
    assert s.jev_api_key.get_secret_value() == "sk-jev-secret"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"decision_backend_order": ["rule", "gpt"]},
        {"decision_modes": {"route": "warn"}},
        {"decision_modes": {"nope": "shadow"}},
        {"jev_model": "jev-latest"},
        {"jev_model": "jev"},
        {"jev_transport": "grpc"},
        {"laya_mode": "remote"},
        {"decision_tau_overrides": {"medium": 0.80}},
        {"decision_tau_overrides": {"critical": 0.99}},
        {"decision_tau_overrides": {"huge": 0.99}},
        {"decision_tau_overrides": {"high": 1.5}},
        {"decision_shadow_sample_rate": 1.5},
        {"decision_eval_audit_rate": -0.1},
        {"decision_max_ece": 0.0},
        {"decision_languages": []},
        {"laya_mode": "http"},  # http needs laya_base_url
    ],
)
def test_invalid_decision_settings_rejected(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Settings(**kwargs)


def test_tau_override_may_raise_the_default() -> None:
    s = Settings(decision_tau_overrides={"medium": 0.95, "low": 0.85})
    assert s.decision_tau_overrides == {"medium": 0.95, "low": 0.85}


def test_shadow_mode_needs_no_calibration(tmp_path: Path) -> None:
    s = Settings(
        decisions_enabled=True,
        decision_routing_enabled=True,
        decision_modes={"route": "shadow"},
        decision_calibration_dir=str(tmp_path),
    )
    assert s.decision_modes == {"route": "shadow"}


def test_enforce_without_calibration_refuses_to_start(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="calibration"):
        Settings(
            decisions_enabled=True,
            decision_routing_enabled=True,
            decision_modes={"route": "enforce"},
            decision_backend_order=["rule", "laya", "llm"],
            decision_calibration_dir=str(tmp_path),
        )


def test_enforce_ignored_while_integration_flag_off(tmp_path: Path) -> None:
    s = Settings(
        decisions_enabled=True,
        decision_modes={"route": "enforce"},
        decision_calibration_dir=str(tmp_path),
    )
    assert s.decision_routing_enabled is False


def _write_record(directory: Path, decision_id: str, model: str, **over: Any) -> None:
    record: dict[str, Any] = {
        "decision_id": decision_id,
        "model": model,
        "method": "temperature",
        "params": {"temperature": 1.5},
        "tau": 0.9,
        "target_precision": 0.97,
        "ece_before": 0.4,
        "ece_after": 0.03,
        "n_by_language": {"en": 500, "es": 400},
        "precision_by_language": {"en": 0.98, "es": 0.975},
        "created_at": "2026-10-05T00:00:00+00:00",
        "version": "v1",
    }
    record.update(over)
    (directory / f"{decision_id}@{model}.json").write_text(json.dumps(record))


def test_enforce_with_valid_calibration_starts(tmp_path: Path) -> None:
    _write_record(tmp_path, "route.supervisor", "laya-multilingual")
    s = Settings(
        decisions_enabled=True,
        decision_routing_enabled=True,
        decision_modes={"route": "enforce"},
        decision_backend_order=["rule", "laya", "llm"],
        decision_calibration_dir=str(tmp_path),
    )
    assert s.decision_modes["route"] == "enforce"


def test_enforce_with_high_ece_refuses_to_start(tmp_path: Path) -> None:
    _write_record(tmp_path, "route.supervisor", "laya-multilingual", ece_after=0.2)
    with pytest.raises(ValidationError, match="ECE"):
        Settings(
            decisions_enabled=True,
            decision_routing_enabled=True,
            decision_modes={"route": "enforce"},
            decision_backend_order=["rule", "laya", "llm"],
            decision_calibration_dir=str(tmp_path),
        )


def test_enforce_needs_calibration_for_every_model_in_order(tmp_path: Path) -> None:
    _write_record(tmp_path, "route.supervisor", "laya-multilingual")
    with pytest.raises(ValidationError, match="jev-1.13.0"):
        Settings(
            decisions_enabled=True,
            decision_routing_enabled=True,
            decision_modes={"route": "enforce"},
            decision_calibration_dir=str(tmp_path),
        )
