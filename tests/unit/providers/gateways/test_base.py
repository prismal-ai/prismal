"""Gateway value objects and port (Phase GW — SPEC-GW-BASE-001..003)."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from pydantic import SecretStr

from prismal.providers.gateways.base import (
    GatewayCallMeta,
    GatewayCapabilities,
    GatewayPort,
    GatewayProfile,
    GenerationStats,
    QuotaSnapshot,
    ReportedCost,
    ResolvedModel,
    WireProtocol,
)
from prismal.providers.gateways.policy import GatewayDataPolicy

SENTINEL = "sk-SENTINEL-do-not-leak"


def _resolved(**kw: Any) -> ResolvedModel:
    base: dict[str, Any] = {
        "litellm_model": "openai/m",
        "api_base": "https://gw/v1",
        "api_key": SecretStr(SENTINEL),
        "extra_headers": {"X-A": "1"},
        "extra_body": {},
        "wire_protocol": WireProtocol.OPENAI_CHAT,
        "gateway": "gw",
        "upstream_model": "m",
    }
    base.update(kw)
    return ResolvedModel(**base)


def test_wire_protocol_values() -> None:
    assert {p.value for p in WireProtocol} == {
        "openai_chat",
        "anthropic_messages",
        "openai_responses",
    }


def test_capabilities_default_false() -> None:
    caps = GatewayCapabilities()
    assert not any(dataclasses.asdict(caps).values())


def test_profile_defaults() -> None:
    p = GatewayProfile(name="gw", base_url="https://gw/v1", auth_ref="gw_key")
    assert p.default_headers == {}
    assert p.data_policy == GatewayDataPolicy()
    assert p.capabilities == GatewayCapabilities()


def test_resolved_model_never_leaks_secret() -> None:
    r = _resolved()
    assert SENTINEL not in repr(r)
    assert SENTINEL not in str(r)
    assert r.api_key.get_secret_value() == SENTINEL
    assert r.owns_model_fallback is False
    assert r.cost_mode == "metered"


def test_value_objects_are_frozen() -> None:
    r = _resolved()
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.gateway = "other"  # type: ignore[misc]


def test_telemetry_value_objects() -> None:
    rc = ReportedCost(cost_usd=0.01, source="usage.cost")
    assert rc.upstream_cost_usd is None and rc.generation_id is None
    q = QuotaSnapshot(
        gateway="g",
        window="credit",
        unit="usd",
        limit=None,
        used=1.0,
        remaining=None,
        resets_at=None,
        source="api",
    )
    assert q.limit is None
    g = GenerationStats(
        generation_id="gen-1",
        total_cost_usd=0.1,
        served_model="m",
        provider_name=None,
        prompt_tokens=1,
        completion_tokens=2,
    )
    assert g.cached_tokens == 0 and g.reasoning_tokens == 0
    meta = GatewayCallMeta(
        gateway="g",
        requested_model="m",
        served_model="m",
        generation_id=None,
        reported_cost_usd=None,
        cost_mode="metered",
    )
    assert meta.fallback_used is False


def test_call_meta_round_trips_through_dict() -> None:
    meta = GatewayCallMeta(
        gateway="openrouter",
        requested_model="a/b",
        served_model="a/c",
        generation_id="gen-1",
        reported_cost_usd=0.002,
        cost_mode="metered",
        fallback_used=True,
    )
    assert GatewayCallMeta.from_dict(meta.to_dict()) == meta
    assert GatewayCallMeta.from_dict({"gateway": 1}) is None
    assert GatewayCallMeta.from_dict("nope") is None


class _FakeGateway:
    @property
    def profile(self) -> GatewayProfile:
        return GatewayProfile(name="fake", base_url="https://f/v1", auth_ref="k")

    def resolve(
        self, model_ref: str, *, agent: str | None, session_id: str | None
    ) -> ResolvedModel:
        return _resolved()

    def reported_cost(self, response: object) -> ReportedCost | None:
        return None

    async def quota(self) -> QuotaSnapshot | None:
        return None

    async def fetch_generation(self, generation_id: str) -> GenerationStats | None:
        return None

    async def astartup_check(self) -> None:
        return None


def test_fake_gateway_satisfies_port() -> None:
    assert isinstance(_FakeGateway(), GatewayPort)
    assert not isinstance(object(), GatewayPort)
