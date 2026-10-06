"""Secret hygiene across the gateway layer (Phase GW — SPEC-GW-BASE-001, ARCH §5.3, GW6-03).

A sentinel key is injected through the secret resolver; it must never appear
in logs, exception text, ``repr`` / ``str`` of any gateway object, request
URLs, ``os.environ`` or a ``RunSummary``-bound ``Usage``.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from structlog.testing import capture_logs

from prismal.core.exceptions import GatewayError
from prismal.providers.gateways.omniroute import OmniRouteGateway
from prismal.providers.gateways.openrouter import OpenRouterGateway
from prismal.providers.gateways.router import GatewayRouter
from prismal.providers.registry import ProviderRegistry

from .conftest import gw_settings

SENTINEL = "sk-SENTINEL-hygiene-7f3a"


def _resolver(ref: str) -> SecretStr | None:
    return SecretStr(SENTINEL)


def _assert_clean(*texts: object) -> None:
    for text in texts:
        assert SENTINEL not in str(text), f"secret leaked in: {str(text)[:200]}"


async def test_openrouter_never_leaks(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(500, json={"error": {"message": "boom"}})

    s = gw_settings(openrouter_enabled=True, openrouter_fallback_models=["a/c"])
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gw = OpenRouterGateway(settings=s, secret_resolver=_resolver, org_id="acme", http=http)
    router = GatewayRouter({"openrouter": gw}, settings=s)
    # The registry constructor runs the legacy vendor-key bridge (exempt, pre-GW);
    # only the gateway path itself must leave os.environ untouched.
    registry = ProviderRegistry(settings=s, gateway_router=router)
    before = dict(os.environ)
    with capture_logs() as logs:
        resolved = gw.resolve("gw:openrouter/a/b", agent="coder", session_id="sess")
        assert await gw.quota() is None
        assert await gw.fetch_generation("gen-1") is None
        llm = registry.get_llm("gw:openrouter/a/b", agent="coder", session_id="sess")
        for bad in ("gw:openrouter/a/b:free", "gw:openrouter/openrouter/auto"):
            with pytest.raises(GatewayError) as exc:
                gw.resolve(bad, agent="coder", session_id="sess")
            _assert_clean(exc.value, repr(exc.value))
    _assert_clean(repr(resolved), str(resolved), repr(gw), repr(llm), str(llm), logs, *seen_urls)
    assert dict(os.environ) == before


async def test_omniroute_never_leaks() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"version": "3.8.40"})

    s = gw_settings(omniroute_enabled=True, omniroute_upstream_attestation="api_keys_only")
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gw = OmniRouteGateway(settings=s, secret_resolver=_resolver, http=http)
    with capture_logs() as logs:
        with pytest.raises(GatewayError) as exc:
            await gw.astartup_check()
        with pytest.raises(GatewayError) as exc2:
            gw.resolve("gw:omniroute/m", agent=None, session_id=None)
    _assert_clean(exc.value, exc2.value, repr(gw), logs)


def test_registry_span_and_logs_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, Any]] = []

    class _Otel:
        def start_span(self, name: str, attributes: dict[str, Any] | None = None) -> Any:
            captured.append(dict(attributes or {}))
            from contextlib import nullcontext

            return nullcontext()

        def increment_counter(self, *a: Any, **k: Any) -> None:
            captured.append({"args": a, "kw": k})

    monkeypatch.setattr("prismal.providers.registry.OTelManager", _Otel)
    s = gw_settings(openrouter_enabled=True)
    gw = OpenRouterGateway(settings=s, secret_resolver=_resolver)
    with capture_logs() as logs:
        ProviderRegistry(
            settings=s, gateway_router=GatewayRouter({"openrouter": gw}, settings=s)
        ).get_llm("gw:openrouter/a/b")
    _assert_clean(captured, logs)
