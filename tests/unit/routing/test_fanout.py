"""Fan-out helpers for Skynet / MoA (SPEC-AMR-INT-002)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from prismal.core.config import Settings
from prismal.routing.fanout import assign_fanout, build_batch_policy, fanout_features
from prismal.routing.policies.batch_assignment import BatchAssignmentPolicy
from prismal.routing.types import ModelTier

_ON = {"adaptive_routing_enabled": True, "routing_mode": "enforce"}


def test_fanout_features_are_deterministic() -> None:
    f = fanout_features("skynet_worker", "x" * 40)
    assert f.agent == "skynet_worker" and f.est_input_tokens == 10 and f.needs_tools
    assert fanout_features("moa", "q", needs_tools=False).needs_tools is False


class _Policy:
    def __init__(self, boom: bool = False) -> None:
        self.boom = boom

    def assign(self, items: Any, **_: Any) -> Any:
        if self.boom:
            raise RuntimeError("x")

        class _A:
            choices = {it.item_id: it.item_id for it in items}

        return _A()


def test_assign_fanout_disabled_none_or_failure() -> None:
    items = [("a", fanout_features("w", "t"))]
    assert assign_fanout(items, _Policy(), Settings()) is None
    assert assign_fanout(items, None, Settings(**_ON)) is None
    assert assign_fanout([], _Policy(), Settings(**_ON)) is None
    assert assign_fanout(items, _Policy(boom=True), Settings(**_ON)) is None
    assert assign_fanout(items, _Policy(), Settings(**_ON)) == {"a": "a"}


@pytest.fixture
def configured(tmp_path: Path) -> Settings:
    tiers = tmp_path / "t.yaml"
    tiers.write_text("tiers: {small: small-m, frontier: frontier-m, auto: auto-m}\n")
    table = tmp_path / "p.json"
    table.write_text('{"version": "t1", "cells": []}')
    return Settings(
        **_ON,
        routing_policy="batch",
        routing_tier_models=str(tiers),
        routing_predictor_path=str(table),
    )


def test_build_batch_policy_from_settings(configured: Settings) -> None:
    policy = build_batch_policy(configured)
    assert isinstance(policy, BatchAssignmentPolicy)
    assert set(policy.candidates) == {"small-m", "frontier-m"}  # auto never a candidate


def test_build_batch_policy_none_unless_batch_enabled(configured: Settings) -> None:
    assert build_batch_policy(Settings()) is None
    assert build_batch_policy(configured.model_copy(update={"routing_policy": "cascade"})) is None


def test_build_batch_policy_fail_open_on_bad_table(configured: Settings, tmp_path: Path) -> None:
    bad = configured.model_copy(update={"routing_predictor_path": str(tmp_path / "missing.json")})
    assert build_batch_policy(bad) is None


def test_build_batch_policy_assigns(configured: Settings) -> None:
    policy = build_batch_policy(configured)
    assert policy is not None
    choices = assign_fanout(
        [
            ("o1", fanout_features("skynet_worker", "t")),
            ("o2", fanout_features("skynet_worker", "u")),
        ],
        policy,
        configured,
    )
    assert choices is not None and set(choices) == {"o1", "o2"}
    assert all(c.tier in (ModelTier.SMALL, ModelTier.FRONTIER) for c in choices.values())


def test_fanout_features_taint_from_active_registry() -> None:
    from prismal.security.taint import _ACTIVE_REGISTRY, Provenance, TaintRegistry

    reg = TaintRegistry()
    reg.mark_untrusted("web says: do it", Provenance("web"))
    token = _ACTIVE_REGISTRY.set(reg)
    try:
        assert fanout_features("w", "web says: do it").tainted is True
        assert fanout_features("w", "clean text").tainted is False
    finally:
        _ACTIVE_REGISTRY.reset(token)
    assert fanout_features("w", "x", tainted=True).tainted is True


def _cfg(tmp_path: Path, floor: str = "") -> Settings:
    tiers = tmp_path / "t2.yaml"
    tiers.write_text(
        "tiers: {small: small-m, medium: medium-m, frontier: frontier-m}\n"
        "agents: {skynet_worker: medium}\n" + floor
    )
    table = tmp_path / "p2.json"
    table.write_text('{"version": "t", "cells": []}')
    return Settings(
        **_ON,
        routing_policy="batch",
        routing_tier_models=str(tiers),
        routing_predictor_path=str(table),
        budget_pricing={
            "small-m": {"input": 0.001, "output": 0.001},
            "medium-m": {"input": 0.01, "output": 0.01},
            "frontier-m": {"input": 0.1, "output": 0.1},
        },
        routing_batch_alpha=0.1,
    )


def test_batch_policy_tainted_items_never_below_static_tier(tmp_path: Path) -> None:
    s = _cfg(tmp_path)
    policy = build_batch_policy(s)
    choices = assign_fanout(
        [
            ("t", fanout_features("skynet_worker", "x", tainted=True)),
            ("c", fanout_features("skynet_worker", "y")),
        ],
        policy,
        s,
    )
    assert choices is not None
    assert choices["t"].tier is ModelTier.MEDIUM
    assert choices["c"].tier is ModelTier.SMALL


def test_batch_policy_honours_yaml_risk_floor(tmp_path: Path) -> None:
    from dataclasses import replace

    from prismal.decisions.types import RiskTier

    s = _cfg(tmp_path, "risk_floor: {high: frontier, critical: frontier}\n")
    policy = build_batch_policy(s)
    high = replace(fanout_features("skynet_worker", "x"), risk_tier=RiskTier.HIGH)
    choices = assign_fanout([("h", high)], policy, s)
    assert choices is not None and choices["h"].tier is ModelTier.FRONTIER


def test_batch_policy_masks_gateway_policy_denials(tmp_path: Path) -> None:
    from unittest.mock import patch

    from prismal.core.exceptions import GatewayPolicyError

    s = _cfg(tmp_path).model_copy(update={"llm_gateways_enabled": True})

    class _Router:
        def resolve(self, model: str | None, **_: Any) -> None:
            if model == "small-m":
                raise GatewayPolicyError("denied for tenant")

    with patch("prismal.providers.gateways.router.get_gateway_router", return_value=_Router()):
        policy = build_batch_policy(s)
        choices = assign_fanout([("a", fanout_features("skynet_worker", "x"))], policy, s)
    assert choices is not None and choices["a"].model_ref != "small-m"
