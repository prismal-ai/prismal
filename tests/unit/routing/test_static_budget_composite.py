"""Static, budget-aware and composite policies (SPEC-AMR-POL-001/002/003)."""

from __future__ import annotations

from pathlib import Path

import pytest

from prismal.budget.types import Budget, BudgetStatus, Usage
from prismal.core.config import Settings
from prismal.core.exceptions import RoutingConfigError
from prismal.decisions.types import RiskTier
from prismal.routing.policies.budget_aware import BudgetAwarePolicy
from prismal.routing.policies.composite import CompositePolicy
from prismal.routing.policies.static import (
    PARITY_AGENTS,
    StaticTierPolicy,
    load_tier_models,
)
from prismal.routing.types import ModelChoice, ModelTier, RoutingOutcome, TaskFeatures

_SHIPPED = Path(__file__).resolve().parents[3] / "config" / "routing_tiers.yaml"

_TIERS = {
    ModelTier.NANO: "nano-m",
    ModelTier.SMALL: "small-m",
    ModelTier.MEDIUM: "medium-m",
    ModelTier.FRONTIER: "frontier-m",
}


def _features(agent: str = "coder", **kw: object) -> TaskFeatures:
    base: dict[str, object] = {
        "agent": agent,
        "intent": None,
        "est_input_tokens": 100,
        "needs_tools": False,
        "needs_structured_output": False,
    }
    base.update(kw)
    return TaskFeatures(**base)  # type: ignore[arg-type]


def _settings(**kw: object) -> Settings:
    return Settings(default_model="default-x", **kw)  # type: ignore[arg-type]


def _status(*, soft: bool = False, hard: bool = False) -> BudgetStatus:
    return BudgetStatus(
        within=not (soft or hard),
        soft_exceeded=soft,
        hard_exceeded=hard,
        breached_dimension="tokens" if (soft or hard) else None,
        usage=Usage(),
        budget=Budget(max_tokens=100),
    )


def _static(**agent_tiers: ModelTier) -> StaticTierPolicy:
    return StaticTierPolicy(settings=_settings(), tier_models=_TIERS, agent_tiers=agent_tiers)


# ── load_tier_models ─────────────────────────────────────────────────────────


def test_shipped_tier_map_loads_and_covers_parity_agents() -> None:
    tiers, agents, floor = load_tier_models(str(_SHIPPED))
    assert len(PARITY_AGENTS) >= 26
    assert set(PARITY_AGENTS) <= set(agents)
    assert all(isinstance(t, ModelTier) for t in agents.values())
    assert tiers == {}  # null tiers = settings.default_model (3.15.0 parity)
    assert floor == {RiskTier.HIGH: ModelTier.MEDIUM, RiskTier.CRITICAL: ModelTier.FRONTIER}


def _write(tmp_path: Path, text: str) -> str:
    p = tmp_path / "tiers.yaml"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_load_configured_tiers(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "tiers: {small: s-model, frontier: gw:openrouter/x}\n"
        "agents: {coder: frontier}\n"
        "risk_floor: {high: small, critical: frontier}\n",
    )
    tiers, agents, floor = load_tier_models(path)
    assert tiers == {ModelTier.SMALL: "s-model", ModelTier.FRONTIER: "gw:openrouter/x"}
    assert agents == {"coder": ModelTier.FRONTIER}
    assert floor[RiskTier.HIGH] is ModelTier.SMALL


@pytest.mark.parametrize(
    "text",
    [
        "- just a list\n",
        "tiers: [a, b]\n",
        "tiers: {gigantic: m}\n",
        "agents: {coder: huge}\n",
        "risk_floor: {critical: auto}\n",
        "risk_floor: {extreme: medium}\n",
        "tiers: {small: 3}\n",
    ],
)
def test_invalid_shape_raises_config_error(tmp_path: Path, text: str) -> None:
    with pytest.raises(RoutingConfigError):
        load_tier_models(_write(tmp_path, text))


def test_missing_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(RoutingConfigError):
        load_tier_models(str(tmp_path / "nope.yaml"))


def test_auto_tier_model_allowed_in_tiers(tmp_path: Path) -> None:
    tiers, _, _ = load_tier_models(
        _write(tmp_path, "tiers: {auto: gw:openrouter/openrouter/auto}\n")
    )
    assert tiers[ModelTier.AUTO] == "gw:openrouter/openrouter/auto"


# ── StaticTierPolicy ─────────────────────────────────────────────────────────


def test_parity_map_yields_default_model_for_every_agent() -> None:
    """POL-001: shipped parity map ⇒ every agent gets the 3.15.0 model (default_model)."""
    tiers, agents, _ = load_tier_models(str(_SHIPPED))
    policy = StaticTierPolicy(settings=_settings(), tier_models=tiers, agent_tiers=agents)
    for agent in PARITY_AGENTS:
        choice = policy.select(_features(agent))
        assert choice.model_ref == "default-x", agent
        assert choice.reason == "static"
        assert choice.policy == "static"


def test_static_uses_agent_tier_model() -> None:
    choice = _static(coder=ModelTier.SMALL).select(_features("coder"))
    assert choice == ModelChoice(model_ref="small-m", tier=ModelTier.SMALL, reason="static")


def test_missing_tier_model_falls_back_to_default_model() -> None:
    policy = StaticTierPolicy(
        settings=_settings(), tier_models={}, agent_tiers={"coder": ModelTier.SMALL}
    )
    assert policy.model_for(ModelTier.SMALL) == "default-x"


def test_unknown_agent_defaults_to_medium() -> None:
    choice = _static().select(_features("mystery"))
    assert choice.tier is ModelTier.MEDIUM
    assert choice.model_ref == "medium-m"


def test_unknown_agent_tier_derived_from_legacy_setting() -> None:
    settings = Settings(default_model="default-x", skynet_worker_model="small-m")
    policy = StaticTierPolicy(settings=settings, tier_models=_TIERS, agent_tiers={})
    assert policy.select(_features("skynet")).tier is ModelTier.SMALL


def test_static_on_outcome_accepts() -> None:
    policy = _static()
    choice = policy.select(_features())
    assert policy.on_outcome(choice, RoutingOutcome(success=False)) is None


def test_static_loads_from_settings_path(tmp_path: Path) -> None:
    path = _write(tmp_path, "tiers: {small: s-model}\nagents: {coder: small}\n")
    policy = StaticTierPolicy(settings=_settings(routing_tier_models=path))
    assert policy.select(_features("coder")).model_ref == "s-model"


def test_static_with_broken_path_is_fail_open(tmp_path: Path) -> None:
    policy = StaticTierPolicy(settings=_settings(routing_tier_models=str(tmp_path / "x.yaml")))
    assert policy.select(_features("coder")).model_ref == "default-x"


# ── BudgetAwarePolicy ────────────────────────────────────────────────────────


def test_budget_none_delegates_unchanged() -> None:
    inner = _static(coder=ModelTier.MEDIUM)
    policy = BudgetAwarePolicy(inner, settings=_settings())
    assert policy.select(_features(), budget=None) == inner.select(_features())


def test_soft_cap_downgrades_exactly_one_tier() -> None:
    policy = BudgetAwarePolicy(_static(coder=ModelTier.FRONTIER), settings=_settings())
    choice = policy.select(_features(), budget=_status(soft=True))
    assert choice.tier is ModelTier.MEDIUM
    assert choice.model_ref == "medium-m"
    assert choice.reason == "budget_soft_cap"
    assert choice.policy == "budget_aware"


def test_soft_cap_never_below_risk_floor() -> None:
    policy = BudgetAwarePolicy(_static(coder=ModelTier.MEDIUM), settings=_settings())
    choice = policy.select(_features(risk_tier=RiskTier.HIGH), budget=_status(soft=True))
    assert choice.tier is ModelTier.MEDIUM


def test_hard_cap_leaves_choice_unchanged() -> None:
    inner = _static(coder=ModelTier.FRONTIER)
    policy = BudgetAwarePolicy(inner, settings=_settings())
    assert policy.select(_features(), budget=_status(hard=True)) == inner.select(_features())


def test_within_budget_unchanged() -> None:
    inner = _static(coder=ModelTier.FRONTIER)
    policy = BudgetAwarePolicy(inner, settings=_settings())
    assert policy.select(_features(), budget=_status()) == inner.select(_features())


def test_soft_cap_at_nano_stays() -> None:
    policy = BudgetAwarePolicy(_static(coder=ModelTier.NANO), settings=_settings())
    assert policy.select(_features(), budget=_status(soft=True)).tier is ModelTier.NANO


def test_soft_cap_never_downgrades_tainted_turn() -> None:
    policy = BudgetAwarePolicy(_static(coder=ModelTier.FRONTIER), settings=_settings())
    choice = policy.select(_features(tainted=True), budget=_status(soft=True))
    assert choice.tier is ModelTier.FRONTIER


def test_soft_cap_auto_is_terminal() -> None:
    tiers = {**_TIERS, ModelTier.AUTO: "auto-m"}
    static = StaticTierPolicy(
        settings=_settings(), tier_models=tiers, agent_tiers={"coder": ModelTier.AUTO}
    )
    choice = BudgetAwarePolicy(static, settings=_settings()).select(
        _features(), budget=_status(soft=True)
    )
    assert choice.tier is ModelTier.AUTO


def test_budget_aware_on_outcome_delegates() -> None:
    class _Esc(StaticTierPolicy):
        def on_outcome(self, choice: ModelChoice, outcome: RoutingOutcome) -> ModelChoice | None:
            return ModelChoice(model_ref="x", tier=ModelTier.FRONTIER, reason="escalation:t")

    inner = _Esc(settings=_settings(), tier_models=_TIERS, agent_tiers={})
    policy = BudgetAwarePolicy(inner, settings=_settings())
    out = policy.on_outcome(policy.select(_features()), RoutingOutcome(success=False))
    assert out is not None and out.model_ref == "x"


# ── CompositePolicy ──────────────────────────────────────────────────────────


class _Fixed:
    def __init__(self, name: str, escalate: ModelChoice | None = None, boom: bool = False) -> None:
        self.name = name
        self._escalate = escalate
        self._boom = boom
        self.offered: list[str] = []

    def select(self, features: TaskFeatures, *, budget: BudgetStatus | None = None) -> ModelChoice:
        if self._boom:
            raise RuntimeError("boom")
        return ModelChoice(model_ref=f"{self.name}-m", tier=ModelTier.SMALL, reason=self.name)

    def on_outcome(self, choice: ModelChoice, outcome: RoutingOutcome) -> ModelChoice | None:
        self.offered.append(self.name)
        return self._escalate


def test_composite_selects_with_outermost() -> None:
    chain = [_Fixed("inner"), _Fixed("outer")]
    assert CompositePolicy(chain).select(_features()).model_ref == "outer-m"


def test_composite_falls_back_inward_when_outer_raises() -> None:
    chain = [_Fixed("inner"), _Fixed("outer", boom=True)]
    assert CompositePolicy(chain).select(_features()).model_ref == "inner-m"


def test_composite_first_escalation_wins_innermost_first() -> None:
    esc_a = ModelChoice(model_ref="a", tier=ModelTier.MEDIUM, reason="escalation:x")
    esc_b = ModelChoice(model_ref="b", tier=ModelTier.FRONTIER, reason="escalation:y")
    inner, mid, outer = _Fixed("inner"), _Fixed("mid", esc_a), _Fixed("outer", esc_b)
    composite = CompositePolicy([inner, mid, outer])
    out = composite.on_outcome(composite.select(_features()), RoutingOutcome(success=False))
    assert out is esc_a
    assert inner.offered == ["inner"] and mid.offered == ["mid"] and outer.offered == []


def test_composite_all_raise_uses_static_fallback() -> None:
    composite = CompositePolicy([_Fixed("a", boom=True)], settings=_settings())
    choice = composite.select(_features())
    assert choice.reason.startswith("fallback:")
    assert choice.model_ref == "default-x"


def test_composite_requires_a_policy() -> None:
    with pytest.raises(ValueError, match="at least one"):
        CompositePolicy([])


def test_tier_map_parsed_once_per_file_version(tmp_path: Path) -> None:
    from unittest.mock import patch

    import prismal.routing.policies.static as static_mod

    path = _write(tmp_path, "tiers: {small: s1}\n")
    static_mod.load_tier_models(path)
    with patch.object(static_mod.yaml, "safe_load", wraps=static_mod.yaml.safe_load) as spy:
        static_mod.load_tier_models(path)
        assert spy.call_count == 0
        import os
        import time

        Path(path).write_text("tiers: {small: s2}\n")
        os.utime(path, (time.time() + 5, time.time() + 5))
        tiers, _, _ = static_mod.load_tier_models(path)
    assert tiers[ModelTier.SMALL] == "s2"


def test_risk_floor_takes_the_stricter_source(tmp_path: Path) -> None:
    path = _write(tmp_path, "risk_floor: {high: frontier, critical: frontier}\n")
    policy = StaticTierPolicy(settings=_settings(routing_tier_models=path))
    assert policy.floor[RiskTier.HIGH] is ModelTier.FRONTIER
    lax = _write(tmp_path, "risk_floor: {high: nano}\n")
    policy = StaticTierPolicy(settings=_settings(routing_tier_models=lax))
    assert policy.floor[RiskTier.HIGH] is ModelTier.MEDIUM
