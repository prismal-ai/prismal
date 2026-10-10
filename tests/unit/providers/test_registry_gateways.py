"""Registry gateway wiring (Phase GW — SPEC-GW-REG-001, DD-GW-003)."""

from __future__ import annotations

import os
from typing import Any

import pytest
from langchain_core.runnables.fallbacks import RunnableWithFallbacks
from pydantic import SecretStr

from prismal.core.config import Settings
from prismal.core.exceptions import (
    GatewayConfigError,
    GatewayPaymentRequired,
    GatewayPolicyError,
)
from prismal.providers.gateways.base import GatewayCapabilities, GatewayProfile
from prismal.providers.gateways.chat_model import GatewayChatLiteLLM
from prismal.providers.gateways.openai_compatible import OpenAICompatibleGateway
from prismal.providers.gateways.router import GatewayRouter, set_gateway_router, use_gateway_router
from prismal.providers.registry import ProviderRegistry


def _gw(
    name: str, *, owns_fallback: bool = False, reports_cost: bool = False
) -> OpenAICompatibleGateway:
    return OpenAICompatibleGateway(
        GatewayProfile(
            name=name,
            base_url=f"https://{name}/v1",
            auth_ref="k",
            default_headers={"X-G": name},
            capabilities=GatewayCapabilities(
                owns_model_fallback=owns_fallback, reports_cost=reports_cost
            ),
        ),
        secret_resolver=lambda ref: SecretStr("sk-gw"),
    )


def _registry(**kw: Any) -> ProviderRegistry:
    base: dict[str, Any] = {"llm_gateways_enabled": True, "retry_attempts": 3}
    base.update(kw)
    s = Settings(**base)
    router = GatewayRouter(
        {
            "g1": _gw("g1"),
            "g2": _gw("g2"),
            "owner": _gw("owner", owns_fallback=True, reports_cost=True),
        },
        settings=s,
    )
    return ProviderRegistry(settings=s, gateway_router=router)


def test_gw_ref_builds_gateway_chat_model() -> None:
    llm = _registry().get_llm("gw:g1/qwen3", agent="coder", session_id="s1", temperature=0.3)
    assert isinstance(llm, GatewayChatLiteLLM)
    assert llm.model == "openai/qwen3"
    assert llm.api_base == "https://g1/v1"
    assert llm.api_key == "sk-gw"
    assert llm.extra_headers == {"X-G": "g1"}
    assert llm.model_kwargs == {}
    assert llm.gateway_name == "g1"
    assert llm.resolved_model_ref == "gw:g1/qwen3"
    assert llm.requested_model == "qwen3"
    assert llm.temperature == 0.3
    assert llm.max_retries == 3


def test_agent_map_routes_without_explicit_model() -> None:
    llm = _registry(llm_gateway_agent_map={"coder": "gw:g2/m"}).get_llm(agent="coder")
    assert isinstance(llm, GatewayChatLiteLLM) and llm.gateway_name == "g2"


def test_gw_default_model_is_resolved() -> None:
    llm = _registry(default_model="gw:g1/m").get_llm()
    assert isinstance(llm, GatewayChatLiteLLM) and llm.gateway_name == "g1"


def test_reports_cost_and_owner_retries() -> None:
    llm = _registry().get_llm("gw:owner/m")
    assert isinstance(llm, GatewayChatLiteLLM)
    assert llm.reports_cost is True
    assert llm.max_retries == 0  # gateway owns model fallback


def test_gw_ref_with_gateways_disabled_is_config_error() -> None:
    with pytest.raises(GatewayConfigError, match="gateways_disabled"):
        ProviderRegistry(settings=Settings()).get_llm("gw:g1/m")


def test_router_found_via_contextvar_and_global() -> None:
    s = Settings(llm_gateways_enabled=True)
    router = GatewayRouter({"g1": _gw("g1")}, settings=s)
    reg = ProviderRegistry(settings=s)
    with use_gateway_router(router):
        assert isinstance(reg.get_llm("gw:g1/m"), GatewayChatLiteLLM)
    set_gateway_router(router)
    try:
        assert isinstance(reg.get_llm("gw:g1/m"), GatewayChatLiteLLM)
    finally:
        set_gateway_router(None)


def test_policy_error_propagates_from_get_llm() -> None:
    with pytest.raises(GatewayConfigError, match="unknown_gateway"):
        _registry().get_llm("gw:nope/m")


# ── DD-GW-003: no double fallback ────────────────────────────────────────────


def test_owner_gateway_returns_primary_unwrapped() -> None:
    llm = _registry(fallback_model="gpt-4o-mini").get_llm_with_fallback("gw:owner/m")
    assert isinstance(llm, GatewayChatLiteLLM)
    assert llm.max_retries == 0


def test_non_owner_gateway_keeps_prismal_fallback() -> None:
    llm = _registry(fallback_model="gpt-4o-mini").get_llm_with_fallback("gw:g1/m")
    assert isinstance(llm, RunnableWithFallbacks)


def test_same_gateway_fallback_excludes_payment_and_policy_errors() -> None:
    llm = _registry(fallback_model="gw:g1/other").get_llm_with_fallback("gw:g1/m")
    assert isinstance(llm, RunnableWithFallbacks)
    handled = llm.exceptions_to_handle
    assert not isinstance(GatewayPaymentRequired(gateway="g1", reason="key_limit"), handled)
    assert not isinstance(GatewayPolicyError("x", gateway="g1"), handled)
    assert isinstance(TimeoutError("t"), handled)


def test_cross_gateway_fallback_handles_everything() -> None:
    llm = _registry(fallback_model="gw:g2/other").get_llm_with_fallback("gw:g1/m")
    assert isinstance(llm, RunnableWithFallbacks)
    assert isinstance(
        GatewayPaymentRequired(gateway="g1", reason="key_limit"), llm.exceptions_to_handle
    )


# ── SPEC-GW-REG-001: no os.environ writes on the gateway path ────────────────


def test_gateway_path_writes_no_environ() -> None:
    reg = _registry()
    before = dict(os.environ)
    reg.get_llm("gw:g1/m", agent="coder", session_id="s")
    reg.get_llm_with_fallback("gw:owner/m")
    assert dict(os.environ) == before
    assert "sk-gw" not in "".join(os.environ.values())


def test_registry_enables_require_parameters_for_provider_gateways() -> None:
    from prismal.providers.gateways.openrouter import OpenRouterGateway

    s = Settings(llm_gateways_enabled=True, openrouter_enabled=True)
    gw = OpenRouterGateway(settings=s, secret_resolver=lambda ref: SecretStr("k"))
    llm = ProviderRegistry(
        settings=s, gateway_router=GatewayRouter({"openrouter": gw}, settings=s)
    ).get_llm("gw:openrouter/a/b")
    assert isinstance(llm, GatewayChatLiteLLM) and llm.require_parameters_on_tools is True
    generic = _registry().get_llm("gw:g1/m")
    assert isinstance(generic, GatewayChatLiteLLM) and generic.require_parameters_on_tools is False
