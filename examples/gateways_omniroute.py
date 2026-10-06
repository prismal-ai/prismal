"""OmniRoute gateway demo (Phase GW) — offline, the health probe is faked.

OmniRoute is self-hosted, so prismal guards it with a fail-closed startup gate:
an API key, an upstream attestation, and a version floor for CVE-2026-88062.
This demo runs the gate against a fake ``/api/monitoring/health`` endpoint:

1. A vulnerable version (3.8.50) is refused and the router disables it.
2. A fixed version (3.8.51) passes and the gateway resolves models.

Run::

    python examples/gateways_omniroute.py
"""

from __future__ import annotations

import asyncio

import httpx
from pydantic import SecretStr

from prismal.core.config import Settings
from prismal.core.exceptions import GatewayPolicyError
from prismal.providers.gateways.omniroute import OmniRouteGateway
from prismal.providers.gateways.router import GatewayRouter


def _gateway(settings: Settings, version: str) -> OmniRouteGateway:
    def health(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"version": version})

    return OmniRouteGateway(
        settings=settings,
        secret_resolver=lambda ref: SecretStr("omni-demo-key"),
        http=httpx.AsyncClient(transport=httpx.MockTransport(health)),
    )


async def main() -> None:
    settings = Settings(
        llm_gateways_enabled=True,
        omniroute_enabled=True,
        omniroute_upstream_attestation="api_keys_only",
    )

    print("1. OmniRoute 3.8.50 (affected by CVE-2026-88062)")
    router = GatewayRouter({"omniroute": _gateway(settings, "3.8.50")}, settings=settings)
    await router.astartup()
    print("   disabled:", router.disabled)
    try:
        router.resolve("gw:omniroute/my-combo", agent=None, session_id=None)
    except GatewayPolicyError as exc:
        print("   resolve refused:", exc.reason)
    await router.aclose()

    print("\n2. OmniRoute 3.8.51")
    router = GatewayRouter({"omniroute": _gateway(settings, "3.8.51")}, settings=settings)
    await router.astartup()
    resolved = router.resolve("gw:omniroute/my-combo", agent=None, session_id=None)
    assert resolved is not None
    print("   litellm model:", resolved.litellm_model, "| api_base:", resolved.api_base)
    await router.aclose()


if __name__ == "__main__":
    asyncio.run(main())
