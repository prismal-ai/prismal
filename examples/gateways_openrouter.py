"""OpenRouter gateway demo (Phase GW) — offline, no request is sent.

Shows what prismal would send for a ``gw:openrouter/...`` reference:

1. The resolved LiteLLM target, headers and ``extra_body`` with the privacy
   defaults (``data_collection="deny"``, ``zdr=true``) and the hashed sticky
   ``session_id``.
2. Gateway-owned model fallback (``models[]``) disabling prismal's own
   fallback (no double fallback).
3. A fail-closed policy gate (a variant outside the allow-list).
4. Metering a gateway response: the billed cost wins (``source="gateway"``).

Run::

    python examples/gateways_openrouter.py
"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage
from pydantic import SecretStr

from prismal.budget.meter import CostMeter
from prismal.core.config import Settings
from prismal.core.exceptions import GatewayPolicyError
from prismal.providers.gateways.base import GatewayCallMeta
from prismal.providers.gateways.openrouter import OpenRouterGateway
from prismal.providers.gateways.router import GatewayRouter
from prismal.providers.registry import ProviderRegistry


def main() -> None:
    settings = Settings(
        llm_gateways_enabled=True,
        openrouter_enabled=True,
        openrouter_app_url="https://example.org",
        openrouter_fallback_models=["deepseek/deepseek-chat"],
    )
    gateway = OpenRouterGateway(
        settings=settings,
        secret_resolver=lambda ref: SecretStr("sk-or-demo"),  # a vault lookup in production
        org_id="acme",
    )

    print("1. Resolve gw:openrouter/anthropic/claude-sonnet-4.5")
    resolved = gateway.resolve(
        "gw:openrouter/anthropic/claude-sonnet-4.5", agent="researcher", session_id="sess-42"
    )
    print("   litellm model:", resolved.litellm_model)
    print("   headers      :", dict(resolved.extra_headers))
    print("   extra_body   :", json.dumps(dict(resolved.extra_body), indent=2))
    print("   repr hides the key:", "sk-or-demo" not in repr(resolved))

    print("\n2. No double fallback")
    router = GatewayRouter({"openrouter": gateway}, settings=settings)
    registry = ProviderRegistry(
        settings=settings.model_copy(update={"fallback_model": "gpt-4o-mini"}),
        gateway_router=router,
    )
    llm = registry.get_llm_with_fallback("gw:openrouter/anthropic/claude-sonnet-4.5")
    print("   returned:", type(llm).__name__, "| max_retries =", getattr(llm, "max_retries", None))

    print("\n3. Fail-closed policy gate")
    try:
        gateway.resolve(
            "gw:openrouter/anthropic/claude-sonnet-4.5:free", agent=None, session_id=None
        )
    except GatewayPolicyError as exc:
        print("   refused:", exc.reason)

    print("\n4. Metering a gateway response")
    meta = GatewayCallMeta(
        gateway="openrouter",
        requested_model="anthropic/claude-sonnet-4.5",
        served_model="anthropic/claude-sonnet-4.5",
        generation_id="gen-demo",
        reported_cost_usd=0.0042,
        cost_mode="metered",
    )
    message = AIMessage(
        content="...",
        usage_metadata={"input_tokens": 1200, "output_tokens": 300, "total_tokens": 1500},
        response_metadata={"prismal_gateway": meta.to_dict()},
    )
    usage = CostMeter(settings=settings).record_response(message, resolved.litellm_model)
    print(f"   cost_usd={usage.cost_usd} source={usage.cost_source} gateway={usage.gateway}")


if __name__ == "__main__":
    main()
