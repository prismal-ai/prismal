"""No double routing with gateways (SPEC-AMR-GW-001, DD-AMR-006)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.runnables.fallbacks import RunnableWithFallbacks
from pydantic import SecretStr

from prismal.core.config import Settings
from prismal.providers.gateways.base import ResolvedModel
from prismal.providers.gateways.chat_model import GatewayChatLiteLLM
from prismal.providers.registry import ProviderRegistry
from prismal.routing.select import clear_routing_run


class _Router:
    """Minimal GatewayRouter stand-in whose target owns model fallback."""

    ledger = None

    def resolve(
        self, model: str | None, *, agent: str | None = None, session_id: str | None = None
    ) -> ResolvedModel | None:
        if not model or not model.startswith("gw:"):
            return None
        upstream = model.split("/", 1)[1]
        return ResolvedModel(
            litellm_model=f"openrouter/{upstream}",
            api_base="https://or/v1",
            api_key=SecretStr("sk"),
            extra_body={"models": ["a/fallback"], "provider": {"zdr": True}},
            gateway="openrouter",
            upstream_model=upstream,
            owns_model_fallback=True,
        )

    def get(self, name: str) -> None:
        return None


@pytest.fixture
def tiers(tmp_path: Path) -> str:
    p = tmp_path / "t.yaml"
    p.write_text(
        "tiers: {small: gw:openrouter/s/small, medium: gw:openrouter/m/medium,"
        " frontier: gw:openrouter/f/frontier}\nagents: {coder: frontier}\n",
        encoding="utf-8",
    )
    return str(p)


def _registry(tiers: str, policy: str) -> ProviderRegistry:
    s = Settings(
        default_model="default-x",
        fallback_model="fallback-x",
        retry_attempts=3,
        llm_gateways_enabled=True,
        adaptive_routing_enabled=True,
        routing_mode="enforce",
        routing_policy=policy,
        routing_tier_models=tiers,
    )
    return ProviderRegistry(settings=s, gateway_router=_Router())  # type: ignore[arg-type]


def _route(tiers: str) -> dict[str, Any]:
    from prismal.routing.features import extract_features

    state = _state()
    return {"features": extract_features(state, "coder"), "state": state}


def _state() -> dict[str, Any]:
    return {"session_id": "gw-s", "messages": [HumanMessage(content="hi")], "metadata": {}}


@pytest.fixture(autouse=True)
def _clean() -> Any:
    yield
    clear_routing_run({"session_id": "gw-s"})


def test_cascade_call_carries_no_gateway_models_list(tiers: str) -> None:
    llm = _registry(tiers, "cascade").get_llm(**_route(tiers))
    assert isinstance(llm, GatewayChatLiteLLM)
    assert llm.requested_model == "s/small"
    body = llm.model_kwargs["extra_body"]
    assert "models" not in body
    assert body["provider"] == {"zdr": True}
    assert llm.max_retries == 3  # owns_model_fallback disabled ⇒ normal retries


def test_cascade_call_has_no_settings_fallback(tiers: str) -> None:
    llm = _registry(tiers, "cascade").get_llm_with_fallback(**_route(tiers))
    assert not isinstance(llm, RunnableWithFallbacks)


def test_static_policy_keeps_gateway_fallback(tiers: str) -> None:
    llm = _registry(tiers, "static").get_llm(**_route(tiers))
    assert isinstance(llm, GatewayChatLiteLLM)
    assert llm.model_kwargs["extra_body"]["models"] == ["a/fallback"]
    assert llm.max_retries == 0


def test_explicit_model_beats_adaptive(tiers: str) -> None:
    llm = _registry(tiers, "cascade").get_llm("plain-model", **_route(tiers))
    assert llm.model == "plain-model"  # type: ignore[attr-defined]
