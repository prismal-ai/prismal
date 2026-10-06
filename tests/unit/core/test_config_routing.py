"""Tests for the adaptive-routing settings (Phase AMR — SPEC-AMR-CFG-001, SEC-001)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from prismal.core.config import Settings


def test_routing_defaults_match_spec() -> None:
    s = Settings()
    assert s.adaptive_routing_enabled is False
    assert s.routing_mode == "shadow"
    assert s.routing_policy == "static"
    assert s.routing_tier_models == "config/routing_tiers.yaml"
    assert s.routing_risk_floor == {"high": "medium", "critical": "frontier"}
    assert s.routing_cascade_start_tier == "small"
    assert s.routing_cascade_max_escalations == 2
    assert s.routing_cascade_tool_error_threshold == 2
    assert s.routing_adequacy_tau == 0.7
    assert s.routing_cache_affinity_enabled is True
    assert s.routing_batch_alpha == 0.8
    assert s.routing_model_concurrency == {}
    assert s.routing_batch_max_iter == 200
    assert s.routing_predictor_path is None
    assert s.plan_cache_enabled is False
    assert s.plan_cache_ttl_s == 86_400
    assert s.plan_cache_intents == []
    assert s.plan_cache_backend == "memory"
    assert s.response_cache_enabled is False
    assert s.batch_api_enabled is False
    assert s.batch_poll_initial_s == 10.0
    assert s.batch_poll_max_s == 300.0
    assert s.batch_price_ratio == 0.5


def test_routing_settings_parse_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRISMAL_ADAPTIVE_ROUTING_ENABLED", "true")
    monkeypatch.setenv("PRISMAL_ROUTING_MODE", "enforce")
    monkeypatch.setenv("PRISMAL_ROUTING_POLICY", "composite")
    monkeypatch.setenv("PRISMAL_ROUTING_MODEL_CONCURRENCY", '{"gpt-4o-mini": 4}')
    monkeypatch.setenv("PRISMAL_PLAN_CACHE_INTENTS", '["researcher"]')
    s = Settings()
    assert s.adaptive_routing_enabled is True
    assert s.routing_mode == "enforce"
    assert s.routing_policy == "composite"
    assert s.routing_model_concurrency == {"gpt-4o-mini": 4}
    assert s.plan_cache_intents == ["researcher"]


@pytest.mark.parametrize("alpha", [0.0, -0.1, 1.5])
def test_alpha_outside_unit_interval_rejected(alpha: float) -> None:
    with pytest.raises(ValidationError, match="ROUTING_BATCH_ALPHA"):
        Settings(routing_batch_alpha=alpha)


def test_alpha_one_accepted() -> None:
    assert Settings(routing_batch_alpha=1.0).routing_batch_alpha == 1.0


def test_negative_concurrency_cap_rejected() -> None:
    with pytest.raises(ValidationError, match="ROUTING_MODEL_CONCURRENCY"):
        Settings(routing_model_concurrency={"m": -1})


def test_unknown_start_tier_rejected() -> None:
    with pytest.raises(ValidationError, match="ROUTING_CASCADE_START_TIER"):
        Settings(routing_cascade_start_tier="huge")


def test_escalations_above_three_rejected() -> None:
    with pytest.raises(ValidationError, match="ROUTING_CASCADE_MAX_ESCALATIONS"):
        Settings(routing_cascade_max_escalations=4)


@pytest.mark.parametrize(
    "floor",
    [{"high": "auto"}, {"high": "giant"}, {"extreme": "medium"}],
)
def test_invalid_risk_floor_rejected(floor: dict[str, str]) -> None:
    with pytest.raises(ValidationError, match="ROUTING_RISK_FLOOR"):
        Settings(routing_risk_floor=floor)


def test_missing_critical_floor_refuses_start_when_enabled() -> None:
    """SEC-001: a missing/invalid floor refuses to start when routing is enabled."""
    with pytest.raises(ValidationError, match="critical"):
        Settings(adaptive_routing_enabled=True, routing_risk_floor={"high": "medium"})


def test_missing_critical_floor_tolerated_when_disabled() -> None:
    s = Settings(routing_risk_floor={"high": "medium"})
    assert s.routing_risk_floor == {"high": "medium"}


def test_batch_policy_requires_predictor_when_enabled() -> None:
    with pytest.raises(ValidationError, match="ROUTING_PREDICTOR_PATH"):
        Settings(adaptive_routing_enabled=True, routing_policy="batch")


def test_batch_policy_with_predictor_accepted(tmp_path: Path) -> None:
    table = tmp_path / "v1.json"
    table.write_text("{}")
    s = Settings(
        adaptive_routing_enabled=True, routing_policy="batch", routing_predictor_path=str(table)
    )
    assert s.routing_policy == "batch"


@pytest.mark.parametrize("ratio", [0.0, 1.5])
def test_batch_price_ratio_bounds(ratio: float) -> None:
    with pytest.raises(ValidationError):
        Settings(batch_price_ratio=ratio)


def test_unknown_policy_and_mode_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(routing_policy="random")
    with pytest.raises(ValidationError):
        Settings(routing_mode="loud")


def test_poll_backoff_bounds() -> None:
    with pytest.raises(ValidationError, match="BATCH_POLL"):
        Settings(batch_poll_initial_s=400.0, batch_poll_max_s=300.0)


def test_invalid_yaml_floor_refuses_start_when_enabled(tmp_path: Path) -> None:
    bad = tmp_path / "tiers.yaml"
    bad.write_text("risk_floor: {critical: auto}\n")
    with pytest.raises(ValidationError, match="risk_floor"):
        Settings(adaptive_routing_enabled=True, routing_tier_models=str(bad))
    assert Settings(routing_tier_models=str(bad)).routing_tier_models == str(bad)


def test_missing_tier_map_tolerated_when_enabled(tmp_path: Path) -> None:
    s = Settings(adaptive_routing_enabled=True, routing_tier_models=str(tmp_path / "none.yaml"))
    assert s.adaptive_routing_enabled is True
