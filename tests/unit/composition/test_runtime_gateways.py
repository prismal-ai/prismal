"""build_runtime gateway composition + per-tenant keys (Phase GW — SPEC-GW-COMP-001, GW6-01/02)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr

from prismal.composition import build_runtime
from prismal.core.config import Settings
from prismal.core.exceptions import GatewayCredentialError, GatewayPolicyError
from prismal.identity.vault import FakeVault
from prismal.providers.gateways.reconcile import CostReconciler
from prismal.providers.gateways.router import (
    GatewayRouter,
    get_gateway_router,
    set_gateway_router,
)

from .test_composition import _reset_global_provider, patched_builders  # noqa: F401

pytestmark = [pytest.mark.unit, pytest.mark.usefixtures("patched_builders")]

_GENERIC = {"my-proxy": {"base_url": "https://proxy.local/v1", "auth_ref": "proxy_key"}}


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {"llm_gateways_enabled": True, "llm_gateway_openai_compatible": _GENERIC}
    base.update(kw)
    return Settings(**base)


def _vault(**entries: str) -> FakeVault:
    vault = FakeVault()
    for ref, value in entries.items():
        vault.store(ref.replace("__", "@"), SecretStr(value))
    return vault


@pytest.fixture(autouse=True)
def _reset_router() -> Any:
    set_gateway_router(None)
    yield
    set_gateway_router(None)


async def test_gateways_none_when_disabled() -> None:
    ctx = await build_runtime(Settings(), mode="context")
    assert ctx.gateways is None
    await ctx.aclose()


async def test_router_composed_with_tenant_scoped_key() -> None:
    vault = _vault(proxy_key__acme="acme-key")
    ctx = await build_runtime(_settings(), org_id="acme", mode="context", gateway_vault=vault)
    assert isinstance(ctx.gateways, GatewayRouter)
    resolved = ctx.gateways.resolve("gw:my-proxy/m", agent=None, session_id=None)
    assert resolved is not None and resolved.api_key.get_secret_value() == "acme-key"
    await ctx.aclose()


async def test_two_tenants_isolated_and_missing_key_never_uses_global() -> None:
    vault = _vault(proxy_key__acme="acme-key", proxy_key__beta="beta-key", proxy_key="global")
    acme = await build_runtime(_settings(), org_id="acme", mode="context", gateway_vault=vault)
    beta = await build_runtime(_settings(), org_id="beta", mode="context", gateway_vault=vault)
    gamma = await build_runtime(_settings(), org_id="gamma", mode="context", gateway_vault=vault)
    keys = [
        ctx.gateways.resolve(
            "gw:my-proxy/m", agent=None, session_id=None
        ).api_key.get_secret_value()  # type: ignore[union-attr]
        for ctx in (acme, beta)
    ]
    assert keys == ["acme-key", "beta-key"]
    with pytest.raises(GatewayCredentialError):
        gamma.gateways.resolve("gw:my-proxy/m", agent=None, session_id=None)  # type: ignore[union-attr]
    for ctx in (acme, beta, gamma):
        await ctx.aclose()


async def test_tenant_fallback_opt_in() -> None:
    vault = _vault(proxy_key="global")
    ctx = await build_runtime(
        _settings(llm_gateway_tenant_key_fallback=True),
        org_id="gamma",
        mode="context",
        gateway_vault=vault,
    )
    resolved = ctx.gateways.resolve("gw:my-proxy/m", agent=None, session_id=None)  # type: ignore[union-attr]
    assert resolved is not None and resolved.api_key.get_secret_value() == "global"
    await ctx.aclose()


async def test_global_mode_installs_and_aclose_clears() -> None:
    ctx = await build_runtime(_settings(), mode="global", gateway_vault=_vault(proxy_key="k"))
    assert get_gateway_router() is ctx.gateways
    await ctx.aclose()
    assert get_gateway_router() is None


async def test_context_mode_leaves_global_untouched() -> None:
    ctx = await build_runtime(_settings(), mode="context", gateway_vault=_vault(proxy_key="k"))
    assert get_gateway_router() is None
    await ctx.aclose()


async def test_reconciler_composed_and_drained() -> None:
    ctx = await build_runtime(
        _settings(gateway_reconcile_enabled=True),
        mode="context",
        gateway_vault=_vault(proxy_key="k"),
    )
    assert ctx.gateways is not None
    assert isinstance(ctx.gateways.reconciler, CostReconciler)
    await ctx.aclose()


async def test_startup_runs_and_disables_failing_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from prismal.providers.gateways.openai_compatible import OpenAICompatibleGateway

    async def boom(self: OpenAICompatibleGateway) -> None:
        raise GatewayPolicyError("blocked", gateway=self.name)

    monkeypatch.setattr(OpenAICompatibleGateway, "astartup_check", boom)
    ctx = await build_runtime(_settings(), mode="context", gateway_vault=_vault(proxy_key="k"))
    assert ctx.gateways is not None and ctx.gateways.disabled == {"my-proxy": "blocked"}
    await ctx.aclose()


async def test_default_vault_without_identity_uses_config_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from prismal.core.config_source import MappingConfigSource, set_config_source

    set_config_source(MappingConfigSource({"proxy_key@acme": "from-source"}))
    try:
        ctx = await build_runtime(_settings(), org_id="acme", mode="context")
        resolved = ctx.gateways.resolve("gw:my-proxy/m", agent=None, session_id=None)  # type: ignore[union-attr]
        assert resolved is not None and resolved.api_key.get_secret_value() == "from-source"
        await ctx.aclose()
    finally:
        set_config_source(None)


async def test_quota_poller_started_for_quota_capable_gateways(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from prismal.providers.gateways.poller import GatewayQuotaPoller

    async def no_poll(self: GatewayQuotaPoller) -> list[Any]:
        return []

    monkeypatch.setattr(GatewayQuotaPoller, "poll_once", no_poll)
    ctx = await build_runtime(
        _settings(openrouter_enabled=True),
        mode="context",
        gateway_vault=_vault(openrouter_api_key="k", proxy_key="k"),
    )
    assert ctx.gateways is not None
    poller = ctx.gateways.quota_poller
    assert isinstance(poller, GatewayQuotaPoller) and poller._task is not None
    await ctx.aclose()
    assert poller._task is None


async def test_no_poller_for_generic_only() -> None:
    ctx = await build_runtime(_settings(), mode="context", gateway_vault=_vault(proxy_key="k"))
    assert ctx.gateways is not None and ctx.gateways.quota_poller is None
    await ctx.aclose()
