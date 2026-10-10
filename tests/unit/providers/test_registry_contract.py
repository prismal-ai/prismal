"""Shared LLM-construction contract (ADR-WAVE-001 §3).

Every wave phase that touches ``ProviderRegistry.get_llm`` **extends** this file
with its own "flag off ⇒ identical kwargs" case and its combination cases;
nothing replaces it. Phases so far: TKE (3.13.0), GW (3.14.0).
"""

from __future__ import annotations

from typing import Any

from langchain_litellm import ChatLiteLLM
from pydantic import SecretStr

from prismal.core.config import Settings
from prismal.providers.gateways.base import GatewayProfile
from prismal.providers.gateways.chat_model import GatewayChatLiteLLM
from prismal.providers.gateways.openai_compatible import OpenAICompatibleGateway
from prismal.providers.gateways.router import GatewayRouter
from prismal.providers.registry import ProviderRegistry

_LEGACY_CALLS: list[dict[str, Any]] = [
    {},
    {"model": "claude-sonnet-4-5"},
    {"model": "gpt-4o", "streaming": True, "temperature": 0.2},
    {"model": "ollama/llama3"},
]


def _dump(llm: Any) -> dict[str, Any]:
    assert type(llm) is ChatLiteLLM
    return llm.model_dump(exclude={"client"})


def _gw_router(settings: Settings) -> GatewayRouter:
    gw = OpenAICompatibleGateway(
        GatewayProfile(name="my-proxy", base_url="https://p/v1", auth_ref="k"),
        secret_resolver=lambda ref: SecretStr("secret"),
    )
    return GatewayRouter({"my-proxy": gw}, settings=settings)


# ── GW: flag off ⇒ identical kwargs (SPEC-GW-SNAP-001) ───────────────────────


def test_gw_flag_off_identical_kwargs_for_every_legacy_call() -> None:
    baseline = ProviderRegistry(settings=Settings())
    gw_off = Settings(
        llm_gateways_enabled=False,
        openrouter_enabled=True,
        llm_gateway_agent_map={"coder": "gw:openrouter/a/b"},
    )
    candidate = ProviderRegistry(settings=gw_off)
    for call in _LEGACY_CALLS:
        assert _dump(baseline.get_llm(**call)) == _dump(
            candidate.get_llm(**call, agent="coder", session_id="s")
        )


def test_gw_on_but_legacy_target_identical_kwargs() -> None:
    baseline = ProviderRegistry(settings=Settings())
    s = Settings(llm_gateways_enabled=True)
    candidate = ProviderRegistry(settings=s, gateway_router=_gw_router(s))
    for call in _LEGACY_CALLS:
        assert _dump(baseline.get_llm(**call)) == _dump(
            candidate.get_llm(**call, agent="researcher", session_id="s")
        )


# ── TKE2 + GW1 combination (ADR-WAVE-001 §3) ─────────────────────────────────


def test_cache_wrapper_wraps_gateway_model_and_keys_are_disjoint() -> None:
    from prismal.providers.prompt_cache import CacheAwareChatModel

    s = Settings(
        llm_gateways_enabled=True, token_efficiency_enabled=True, prompt_cache_enabled=True
    )
    llm = ProviderRegistry(settings=s, gateway_router=_gw_router(s)).get_llm(
        "gw:my-proxy/claude-x", agent="coder", session_id="s"
    )
    assert isinstance(llm, CacheAwareChatModel)
    assert isinstance(llm.inner, GatewayChatLiteLLM)
    gw_keys = {"provider", "models", "plugins", "session_id"}
    tke_keys = {"prompt_cache_key", "prompt_cache_retention", "cache_control", "reasoning_effort"}
    assert gw_keys.isdisjoint(tke_keys)
    assert set(llm.inner.model_kwargs.get("extra_body", {})) <= gw_keys


def test_amr5_compute_cost_batch_false_default_equals_previous() -> None:
    """AMR5-05: ``batch=False`` (the default) leaves every precedence row unchanged."""
    from prismal.providers.cost import compute_cost_usd

    settings = Settings(budget_pricing={"acme/contract": {"input": 1.0, "output": 2.0}})
    for kwargs in (
        {},
        {"reported_cost_usd": 0.3},
        {"cost_mode": "subscription"},
        {"cache_read_tokens": 100},
    ):
        assert compute_cost_usd(
            "acme/contract", 1000, 500, settings=settings, batch=False, **kwargs
        ) == compute_cost_usd("acme/contract", 1000, 500, settings=settings, **kwargs)


# ── AMR1: flag off ⇒ identical kwargs; parity map ⇒ identical kwargs ─────────


def test_amr_flag_off_identical_kwargs_even_with_features_and_state() -> None:
    from langchain_core.messages import HumanMessage

    from prismal.routing.types import TaskFeatures

    baseline = ProviderRegistry(settings=Settings())
    candidate = ProviderRegistry(settings=Settings(adaptive_routing_enabled=False))
    features = TaskFeatures(
        agent="coder",
        intent=None,
        est_input_tokens=1,
        needs_tools=True,
        needs_structured_output=False,
    )
    state = {"session_id": "amr-c", "messages": [HumanMessage(content="hi")], "metadata": {}}
    for call in _LEGACY_CALLS:
        assert _dump(baseline.get_llm(**call)) == _dump(
            candidate.get_llm(**call, agent="coder", features=features, state=state)
        )
    assert "routing" not in state["metadata"]


def test_amr_parity_map_enforced_identical_kwargs_for_every_agent() -> None:
    """SPEC-AMR-POL-001: shipped parity map ⇒ the 3.15.0 ChatLiteLLM args for all agents."""
    from langchain_core.messages import HumanMessage

    from prismal.routing.outcome import llm_route_kwargs
    from prismal.routing.policies.static import PARITY_AGENTS
    from prismal.routing.select import clear_routing_run

    baseline = _dump(ProviderRegistry(settings=Settings()).get_llm())
    enabled = Settings(adaptive_routing_enabled=True, routing_mode="enforce")
    registry = ProviderRegistry(settings=enabled)
    state = {"session_id": "amr-parity", "messages": [HumanMessage(content="hi")], "metadata": {}}
    try:
        for agent in PARITY_AGENTS:
            assert (
                _dump(registry.get_llm(**llm_route_kwargs(agent, state, settings=enabled)))
                == baseline
            ), agent
    finally:
        clear_routing_run(state)


def test_amr_enforced_tier_model_and_fail_open(tmp_path: Any) -> None:
    from unittest.mock import patch

    from langchain_core.messages import HumanMessage

    from prismal.routing.outcome import llm_route_kwargs
    from prismal.routing.select import clear_routing_run

    tiers = tmp_path / "t.yaml"
    tiers.write_text("tiers: {frontier: frontier-m}\nagents: {coder: frontier}\n")
    s = Settings(
        default_model="default-x",
        adaptive_routing_enabled=True,
        routing_mode="enforce",
        routing_tier_models=str(tiers),
    )
    state = {"session_id": "amr-e", "messages": [HumanMessage(content="hi")], "metadata": {}}
    registry = ProviderRegistry(settings=s)
    try:
        assert (
            registry.get_llm(**llm_route_kwargs("coder", state, settings=s)).model == "frontier-m"
        )  # type: ignore[attr-defined]
        assert (
            registry.get_llm("explicit-m", **llm_route_kwargs("coder", state, settings=s)).model
            == "explicit-m"
        )  # type: ignore[attr-defined]
        with patch("prismal.routing.select.select_model", side_effect=RuntimeError("x")):
            assert (
                registry.get_llm(**llm_route_kwargs("coder", state, settings=s)).model
                == "default-x"
            )  # type: ignore[attr-defined]
    finally:
        clear_routing_run(state)


def test_amr_shadow_mode_keeps_default_model(tmp_path: Any) -> None:
    from langchain_core.messages import HumanMessage

    from prismal.routing.outcome import llm_route_kwargs
    from prismal.routing.select import clear_routing_run

    tiers = tmp_path / "t.yaml"
    tiers.write_text("tiers: {frontier: frontier-m}\nagents: {coder: frontier}\n")
    s = Settings(
        default_model="default-x", adaptive_routing_enabled=True, routing_tier_models=str(tiers)
    )
    state = {"session_id": "amr-sh", "messages": [HumanMessage(content="hi")], "metadata": {}}
    try:
        llm = ProviderRegistry(settings=s).get_llm(**llm_route_kwargs("coder", state, settings=s))
        assert _dump(llm) == _dump(
            ProviderRegistry(settings=Settings(default_model="default-x")).get_llm()
        )
        assert state["metadata"]["routing"]["shadow"]["coder"]["model_ref"] == "frontier-m"
    finally:
        clear_routing_run(state)


def test_amr_features_without_state_use_feature_agent(tmp_path: Any) -> None:
    from prismal.routing.select import clear_routing_run
    from prismal.routing.types import TaskFeatures

    tiers = tmp_path / "t.yaml"
    tiers.write_text("tiers: {small: small-m}\nagents: {file_manager: small}\n")
    s = Settings(
        adaptive_routing_enabled=True, routing_mode="enforce", routing_tier_models=str(tiers)
    )
    features = TaskFeatures(
        agent="file_manager",
        intent=None,
        est_input_tokens=1,
        needs_tools=True,
        needs_structured_output=False,
    )
    try:
        assert ProviderRegistry(settings=s).get_llm(features=features).model == "small-m"  # type: ignore[attr-defined]
    finally:
        clear_routing_run({})
