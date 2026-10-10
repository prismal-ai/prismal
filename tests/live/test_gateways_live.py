"""Live gateway smoke tests (Phase GW — GW6-04). Opt-in: ``-m live_api`` + real keys.

They spend real (free-tier) requests, so they are skipped unless the key is
configured. ``:free`` must be allowed explicitly because it is not in the
default variant allow-list (DD-GW-004).
"""

from __future__ import annotations

import pytest

from prismal.budget.meter import CostMeter
from prismal.budget.usage import extract_gateway_meta
from prismal.core.config import Settings, get_settings
from prismal.providers.gateways.router import build_gateway_router
from prismal.providers.registry import ProviderRegistry

pytestmark = pytest.mark.live_api

FREE_MODEL = "gw:openrouter/meta-llama/llama-3.3-8b-instruct:free"


def _settings(**kw: object) -> Settings:
    return get_settings().model_copy(update={"llm_gateways_enabled": True, **kw})


@pytest.mark.skipif(
    not get_settings().openrouter_api_key.get_secret_value(),
    reason="PRISMAL_OPENROUTER_API_KEY not set",
)
async def test_openrouter_free_call_reports_usage_cost_and_served_model() -> None:
    s = _settings(openrouter_enabled=True, openrouter_allowed_variants=["free"])
    router = build_gateway_router(s)
    assert router is not None
    llm = ProviderRegistry(settings=s, gateway_router=router).get_llm(
        FREE_MODEL, agent="coder", session_id="live-smoke"
    )
    msg = await llm.ainvoke("Reply with the single word: pong")
    meta = extract_gateway_meta(msg)
    assert meta is not None and meta.served_model
    usage = CostMeter(settings=s).record_response(msg, FREE_MODEL)
    assert usage.total_tokens > 0
    assert usage.cost_source in {"gateway", "litellm", "none"}
    await router.aclose()


@pytest.mark.skipif(
    not get_settings().opencode_api_key.get_secret_value(),
    reason="PRISMAL_OPENCODE_API_KEY not set",
)
async def test_opencode_zen_call_optional() -> None:
    s = _settings(opencode_zen_enabled=True)
    router = build_gateway_router(s)
    assert router is not None
    zen = router.get("opencode")
    assert zen is not None
    model_id = next(iter(getattr(zen, "protocols", {}) or {}), None)
    if model_id is None:
        pytest.skip("no Zen model in the protocol map")
    llm = ProviderRegistry(settings=s, gateway_router=router).get_llm(f"gw:opencode/{model_id}")
    msg = await llm.ainvoke("Reply with the single word: pong")
    assert extract_gateway_meta(msg) is not None
    await router.aclose()
