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
