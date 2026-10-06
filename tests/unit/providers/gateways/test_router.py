"""GatewayRouter (Phase GW — SPEC-GW-RTR-001)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import SecretStr

from prismal.core.exceptions import GatewayConfigError, GatewayCredentialError, GatewayPolicyError
from prismal.providers.gateways.base import GatewayProfile, QuotaSnapshot
from prismal.providers.gateways.openai_compatible import OpenAICompatibleGateway
from prismal.providers.gateways.router import (
    GatewayRouter,
    build_gateway_router,
    get_gateway_router,
    set_gateway_router,
    settings_secret_resolver,
    use_gateway_router,
    vault_secret_resolver,
)

from .conftest import gw_settings


def _generic(name: str = "my-proxy") -> OpenAICompatibleGateway:
    return OpenAICompatibleGateway(
        GatewayProfile(name=name, base_url="https://p/v1", auth_ref="k"),
        secret_resolver=lambda ref: SecretStr("secret"),
    )


def _router(**settings_kw: Any) -> GatewayRouter:
    return GatewayRouter({"my-proxy": _generic()}, settings=gw_settings(**settings_kw))


def test_target_precedence() -> None:
    r = _router(llm_gateway_agent_map={"coder": "gw:my-proxy/mapped"})
    assert r.target_for("gw:my-proxy/explicit", agent="coder") == "gw:my-proxy/explicit"
    assert r.target_for(None, agent="coder") == "gw:my-proxy/mapped"
    assert r.target_for("gpt-4o", agent="coder") is None  # explicit legacy model wins
    assert r.target_for(None, agent="researcher") is None
    assert r.target_for(None, agent=None) is None


def test_resolve_legacy_returns_none() -> None:
    assert _router().resolve("gpt-4o", agent=None, session_id=None) is None


def test_resolve_gw_ref() -> None:
    resolved = _router().resolve("gw:my-proxy/qwen", agent="coder", session_id="s")
    assert resolved is not None and resolved.litellm_model == "openai/qwen"


def test_resolve_unknown_gateway() -> None:
    with pytest.raises(GatewayConfigError, match="unknown_gateway"):
        _router().resolve("gw:nope/m", agent=None, session_id=None)


def test_resolve_does_no_network_io(no_network: Any) -> None:
    guard, client = no_network
    gw = OpenAICompatibleGateway(
        GatewayProfile(name="my-proxy", base_url="https://p/v1", auth_ref="k"),
        secret_resolver=lambda ref: SecretStr("secret"),
        http=client,
    )
    GatewayRouter({"my-proxy": gw}, settings=gw_settings()).resolve(
        "gw:my-proxy/m", agent=None, session_id=None
    )
    assert guard.requests == []


class _FailingStartup(OpenAICompatibleGateway):
    async def astartup_check(self) -> None:
        raise GatewayPolicyError("omniroute_vulnerable_version", gateway=self.name)


async def test_astartup_disables_failing_gateway() -> None:
    bad = _FailingStartup(
        GatewayProfile(name="bad", base_url="https://b/v1", auth_ref="k"),
        secret_resolver=lambda ref: SecretStr("s"),
    )
    router = GatewayRouter({"bad": bad, "my-proxy": _generic()}, settings=gw_settings())
    await router.astartup()
    assert router.get("bad") is None
    assert router.disabled == {"bad": "omniroute_vulnerable_version"}
    with pytest.raises(GatewayPolicyError) as exc:
        router.resolve("gw:bad/m", agent=None, session_id=None)
    assert exc.value.reason == "omniroute_vulnerable_version"
    assert router.get("my-proxy") is not None


class _QuotaGateway(OpenAICompatibleGateway):
    async def quota(self) -> QuotaSnapshot | None:
        return QuotaSnapshot(
            gateway=self.name,
            window="credit",
            unit="usd",
            limit=10.0,
            used=1.0,
            remaining=9.0,
            resets_at=None,
            source="api",
        )


class _BrokenQuota(OpenAICompatibleGateway):
    async def quota(self) -> QuotaSnapshot | None:
        raise RuntimeError("down")


async def test_quotas_collects_and_is_fail_open() -> None:
    q = _QuotaGateway(
        GatewayProfile(name="q", base_url="https://q/v1", auth_ref="k"),
        secret_resolver=lambda ref: SecretStr("s"),
    )
    b = _BrokenQuota(
        GatewayProfile(name="b", base_url="https://b/v1", auth_ref="k"),
        secret_resolver=lambda ref: SecretStr("s"),
    )
    router = GatewayRouter({"q": q, "b": b, "my-proxy": _generic()}, settings=gw_settings())
    snaps = await router.quotas()
    assert [s.gateway for s in snaps] == ["q"]
    await router.aclose()


def test_build_returns_none_when_disabled() -> None:
    assert build_gateway_router(gw_settings(llm_gateways_enabled=False)) is None


def test_build_includes_generic_gateways() -> None:
    s = gw_settings(
        llm_gateway_openai_compatible={"my-proxy": {"base_url": "https://p/v1", "auth_ref": "k"}}
    )
    router = build_gateway_router(s, secret_resolver=lambda ref: SecretStr("s"))
    assert router is not None and router.get("my-proxy") is not None
    assert router.get("openrouter") is None  # not enabled


def test_contextvar_beats_global() -> None:
    global_router = _router()
    tenant_router = _router()
    set_gateway_router(global_router)
    try:
        assert get_gateway_router() is global_router
        with use_gateway_router(tenant_router):
            assert get_gateway_router() is tenant_router
        assert get_gateway_router() is global_router
    finally:
        set_gateway_router(None)
    assert get_gateway_router() is None


async def test_contextvar_is_task_isolated() -> None:
    a, b = _router(), _router()

    async def bound(router: GatewayRouter) -> GatewayRouter | None:
        with use_gateway_router(router):
            await asyncio.sleep(0)
            return get_gateway_router()

    assert await asyncio.gather(bound(a), bound(b)) == [a, b]


def test_settings_secret_resolver_maps_builtin_refs() -> None:
    s = gw_settings(openrouter_api_key="or-key", opencode_api_key="oc-key")
    resolve = settings_secret_resolver(s)
    assert resolve("openrouter_api_key").get_secret_value() == "or-key"  # type: ignore[union-attr]
    assert resolve("opencode_api_key").get_secret_value() == "oc-key"  # type: ignore[union-attr]
    assert resolve("omniroute_api_key") is None  # empty ⇒ missing


class _Vault:
    def __init__(self, entries: dict[str, str]) -> None:
        self.entries = entries
        self.asked: list[str] = []

    def resolve(self, credential_ref: str, *, scopes: Any = ()) -> Any:
        from prismal.core.exceptions import CredentialResolutionError
        from prismal.identity.types import Credential

        self.asked.append(credential_ref)
        if credential_ref not in self.entries:
            raise CredentialResolutionError(credential_ref)
        return Credential(ref=credential_ref, value=SecretStr(self.entries[credential_ref]))


def test_vault_resolver_is_tenant_scoped_without_global_fallback() -> None:
    vault = _Vault({"openrouter_api_key@acme": "acme-key", "openrouter_api_key": "global"})
    s = gw_settings()
    acme = vault_secret_resolver(vault, org_id="acme", settings=s)
    other = vault_secret_resolver(vault, org_id="other", settings=s)
    assert acme("openrouter_api_key").get_secret_value() == "acme-key"  # type: ignore[union-attr]
    assert other("openrouter_api_key") is None  # no silent global billing


def test_vault_resolver_global_fallback_is_opt_in() -> None:
    vault = _Vault({"openrouter_api_key": "global"})
    s = gw_settings(llm_gateway_tenant_key_fallback=True)
    other = vault_secret_resolver(vault, org_id="other", settings=s)
    assert other("openrouter_api_key").get_secret_value() == "global"  # type: ignore[union-attr]


def test_missing_tenant_key_surfaces_as_credential_error() -> None:
    vault = _Vault({})
    gw = OpenAICompatibleGateway(
        GatewayProfile(name="my-proxy", base_url="https://p/v1", auth_ref="k"),
        secret_resolver=vault_secret_resolver(vault, org_id="acme", settings=gw_settings()),
    )
    with pytest.raises(GatewayCredentialError):
        GatewayRouter({"my-proxy": gw}, settings=gw_settings(), org_id="acme").resolve(
            "gw:my-proxy/m", agent=None, session_id=None
        )


class _CrashingStartup(OpenAICompatibleGateway):
    async def astartup_check(self) -> None:
        raise RuntimeError("probe crashed")


async def test_astartup_fails_closed_on_unexpected_error(otel: Any) -> None:
    """Security review: a crashing startup gate disables the gateway (never fail-open)."""
    crash = _CrashingStartup(
        GatewayProfile(name="crash", base_url="https://c/v1", auth_ref="k"),
        secret_resolver=lambda ref: SecretStr("s"),
    )
    router = GatewayRouter({"crash": crash}, settings=gw_settings())
    await router.astartup()
    assert router.get("crash") is None
    assert router.disabled == {"crash": "startup_check_failed"}
    assert otel.counts["gateway_errors"] == [{"gateway": "crash", "kind": "startup"}]
    with pytest.raises(GatewayPolicyError):
        router.resolve("gw:crash/m", agent=None, session_id=None)
