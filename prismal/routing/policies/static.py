"""``StaticTierPolicy`` — agent → tier → model ref (Phase AMR — SPEC-AMR-POL-001).

The shipped ``config/routing_tiers.yaml`` is a *parity map*: every tier is
``null`` (= ``settings.default_model``), so enabling routing with the static
policy reproduces 3.15.0 exactly (DD-AMR-002). Operators fill in tier models to
let the other policies trade cost for capability.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from prismal.core.config import get_settings
from prismal.core.exceptions import RoutingConfigError
from prismal.core.logging import get_logger
from prismal.decisions.types import RiskTier
from prismal.routing.types import TIER_ORDER, ModelChoice, ModelTier, max_tier

if TYPE_CHECKING:
    from collections.abc import Mapping

    from prismal.budget.types import BudgetStatus
    from prismal.core.config import Settings
    from prismal.routing.types import RoutingOutcome, TaskFeatures

logger = get_logger(__name__)

RiskFloor = dict[RiskTier, ModelTier]

#: The graph nodes whose model choice the parity map must preserve (3.15.0).
PARITY_AGENTS: tuple[str, ...] = (
    "supervisor",
    "researcher",
    "coder",
    "codeact",
    "cua",
    "rag_agent",
    "planner",
    "critic",
    "data_analyst",
    "file_manager",
    "skill_manager",
    "cron_manager",
    "dev_pipeline",
    "ml_pipeline",
    "financial_analyst",
    "parallel_researcher",
    "meta_learner",
    "skill_creator",
    "domain_supervisor",
    "network_supervisor",
    "tot_agent",
    "debate_agent",
    "constitutional_filter",
    "lats_agent",
    "llm_compiler",
    "mixture_agent",
)

#: Unknown agent → legacy per-feature model setting used to derive its tier.
_LEGACY_MODEL_SETTINGS: dict[str, str] = {
    "skynet": "skynet_worker_model",
    "skynet_worker": "skynet_worker_model",
    "skynet_planner": "skynet_planner_model",
    "kokoro": "kokoro_judge_model",
    "kokoro_judge": "kokoro_judge_model",
    "eval_judge": "eval_judge_model",
    "blind_review_spec": "blind_review_spec_model",
    "blind_review_implementer": "blind_review_implementer_model",
    "blind_review_reviewer_a": "blind_review_reviewer_a_model",
    "blind_review_reviewer_b": "blind_review_reviewer_b_model",
}

#: Parsed tier maps keyed by (path, mtime, size): one parse per file version.
_TIER_CACHE: dict[
    tuple[str, int, int], tuple[dict[ModelTier, str], dict[str, ModelTier], RiskFloor]
] = {}

_ORDERED = {t.value for t in TIER_ORDER}
_ALL_TIERS = _ORDERED | {ModelTier.AUTO.value}
_RISKS = {r.value for r in RiskTier}


def _tier(value: Any, *, where: str, allow_auto: bool) -> ModelTier:
    allowed = _ALL_TIERS if allow_auto else _ORDERED
    if not isinstance(value, str) or value not in allowed:
        raise RoutingConfigError(f"{where}: {value!r} is not one of {sorted(allowed)}")
    return ModelTier(value)


def _mapping(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key) or {}
    if not isinstance(value, dict):
        raise RoutingConfigError(f"routing tiers: {key!r} must be a mapping")
    return value


def load_tier_models(
    path: str | None = None,
) -> tuple[dict[ModelTier, str], dict[str, ModelTier], RiskFloor]:
    """Load ``config/routing_tiers.yaml`` → ``(tier_models, agent_tiers, risk_floor)``.

    ``null`` tier entries are omitted (the policy resolves them to
    ``settings.default_model``). A missing file or an invalid shape raises
    :class:`RoutingConfigError` at load time.
    """
    path_str = path if path is not None else get_settings().routing_tier_models
    try:
        stat = Path(path_str).stat()
    except OSError as exc:
        raise RoutingConfigError(f"routing tiers unreadable at {path_str!r}: {exc}") from exc
    cache_key = (str(Path(path_str).resolve()), stat.st_mtime_ns, stat.st_size)
    cached = _TIER_CACHE.get(cache_key)
    if cached is not None:
        return dict(cached[0]), dict(cached[1]), dict(cached[2])
    parsed = _parse_tier_file(path_str)
    if len(_TIER_CACHE) >= 64:
        _TIER_CACHE.clear()
    _TIER_CACHE[cache_key] = parsed
    return dict(parsed[0]), dict(parsed[1]), dict(parsed[2])


def _parse_tier_file(
    path_str: str,
) -> tuple[dict[ModelTier, str], dict[str, ModelTier], RiskFloor]:
    try:
        raw = yaml.safe_load(Path(path_str).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RoutingConfigError(f"routing tiers unreadable at {path_str!r}: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise RoutingConfigError("routing tiers: top level must be a mapping")

    tier_models: dict[ModelTier, str] = {}
    for name, ref in _mapping(raw, "tiers").items():
        tier = _tier(name, where="tiers", allow_auto=True)
        if ref is None:
            continue
        if not isinstance(ref, str) or not ref:
            raise RoutingConfigError(f"tiers.{name}: model ref must be a non-empty string")
        tier_models[tier] = ref

    agent_tiers = {
        str(agent): _tier(t, where=f"agents.{agent}", allow_auto=True)
        for agent, t in _mapping(raw, "agents").items()
    }

    floor: RiskFloor = {}
    for risk, t in _mapping(raw, "risk_floor").items():
        if risk not in _RISKS:
            raise RoutingConfigError(f"risk_floor: {risk!r} is not one of {sorted(_RISKS)}")
        floor[RiskTier(risk)] = _tier(t, where=f"risk_floor.{risk}", allow_auto=False)
    return tier_models, agent_tiers, floor


def resolve_risk_floor(
    settings: Settings, base: Mapping[RiskTier, ModelTier] | None = None
) -> RiskFloor:
    """YAML floor combined with ``settings.routing_risk_floor``: the stricter wins.

    Floors are fail-closed (SEC-001), so neither source can lower the other.
    """
    floor: RiskFloor = dict(base or {})
    for risk, tier in settings.routing_risk_floor.items():
        r, t = RiskTier(risk), ModelTier(tier)
        current = floor.get(r)
        floor[r] = t if current is None else max_tier(current, t)
    return floor


class StaticTierPolicy:
    """Fixed agent → tier → model ref; the fail-open fallback of every policy."""

    name = "static"

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        tier_models: Mapping[ModelTier, str] | None = None,
        agent_tiers: Mapping[str, ModelTier] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        yaml_floor: RiskFloor = {}
        if tier_models is None or agent_tiers is None:
            try:
                loaded_tiers, loaded_agents, yaml_floor = load_tier_models(
                    self._settings.routing_tier_models
                )
            except RoutingConfigError as exc:
                # Fail-open on cost: every agent keeps settings.default_model.
                logger.warning("routing.tier_map_unavailable", error=str(exc))
                loaded_tiers, loaded_agents = {}, {}
            tier_models = loaded_tiers if tier_models is None else tier_models
            agent_tiers = loaded_agents if agent_tiers is None else agent_tiers
        self._tier_models = dict(tier_models)
        self._agent_tiers = dict(agent_tiers)
        self.floor: RiskFloor = resolve_risk_floor(self._settings, yaml_floor)

    def model_for(self, tier: ModelTier) -> str:
        """Model ref for *tier*; unset tiers resolve to ``settings.default_model``."""
        return self._tier_models.get(tier) or self._settings.default_model

    def has_tier(self, tier: ModelTier) -> bool:
        """True when *tier* has an explicit model configured."""
        return tier in self._tier_models

    def tier_for_agent(self, agent: str) -> ModelTier:
        """The agent's static tier (map → legacy model setting → medium)."""
        tier = self._agent_tiers.get(agent)
        if tier is not None:
            return tier
        legacy_attr = _LEGACY_MODEL_SETTINGS.get(agent)
        legacy = getattr(self._settings, legacy_attr, None) if legacy_attr else None
        if legacy:
            for candidate, ref in self._tier_models.items():
                if ref == legacy:
                    return candidate
        return ModelTier.MEDIUM

    def select(self, features: TaskFeatures, *, budget: BudgetStatus | None = None) -> ModelChoice:  # noqa: ARG002 — port signature
        """The agent's static tier and its model (reason ``static``)."""
        tier = self.tier_for_agent(features.agent)
        return ModelChoice(model_ref=self.model_for(tier), tier=tier, reason="static")

    def on_outcome(self, choice: ModelChoice, outcome: RoutingOutcome) -> None:
        """Static routing never escalates."""
