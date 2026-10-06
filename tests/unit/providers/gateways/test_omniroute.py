"""OmniRoute gateway + compliance guard (Phase GW4 — SPEC-GW-OMNI-001)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from prismal.core.config import Settings
from prismal.core.exceptions import GatewayCredentialError, GatewayPolicyError
from prismal.providers.gateways.base import GatewayPort, WireProtocol
from prismal.providers.gateways.omniroute import OmniRouteGateway, build_from_settings
from prismal.providers.gateways.router import GatewayRouter

from .conftest import gw_settings

SENTINEL = "sk-omni-SENTINEL-4242"


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "omniroute_enabled": True,
        "omniroute_upstream_attestation": "api_keys_only",
    }
    base.update(kw)
    return gw_settings(**base)


def _key(ref: str) -> SecretStr | None:
    return SecretStr(SENTINEL) if ref == "omniroute_api_key" else None


def _gw(
    settings: Settings | None = None,
    *,
    http: httpx.AsyncClient | None = None,
    resolver: Callable[[str], SecretStr | None] = _key,
) -> OmniRouteGateway:
    return OmniRouteGateway(settings=settings or _settings(), secret_resolver=resolver, http=http)


async def _started(settings: Settings | None = None) -> OmniRouteGateway:
    """A gateway whose startup gate passed (probe reports the default floor)."""
    gw = _gw(settings, http=_probe(json={"version": "3.8.51"}))
    await gw.astartup_check()
    return gw


def _denials(otel: Any) -> list[str]:
    return [d["reason"] for d in otel.counts.get("gateway_policy_denials", [])]


# ── GW4-02: resolution ───────────────────────────────────────────────────────


def test_is_gateway_port_with_profile() -> None:
    gw = _gw()
    assert isinstance(gw, GatewayPort)
    assert gw.name == "omniroute"
    assert gw.profile.base_url == "http://localhost:20128/v1"
    assert gw.profile.auth_ref == "omniroute_api_key"
    assert gw.profile.capabilities.enforces_data_policy is False


async def test_resolve_maps_to_openai_prefix() -> None:
    gw = await _started(_settings(omniroute_base_url="http://10.0.0.5:20128/v1"))
    resolved = gw.resolve("gw:omniroute/claude-combo", agent="coder", session_id="s")
    assert resolved.litellm_model == "openai/claude-combo"
    assert resolved.api_base == "http://10.0.0.5:20128/v1"
    assert resolved.api_key.get_secret_value() == SENTINEL
    assert resolved.wire_protocol is WireProtocol.OPENAI_CHAT
    assert resolved.gateway == "omniroute"


async def test_resolve_key_removed_after_startup_raises_credential_error() -> None:
    present = {"key": True}
    gw = _gw(
        http=_probe(json={"version": "3.8.51"}),
        resolver=lambda ref: SecretStr(SENTINEL) if present["key"] else None,
    )
    await gw.astartup_check()
    present["key"] = False  # e.g. revoked in the vault
    with pytest.raises(GatewayCredentialError) as exc:
        gw.resolve("gw:omniroute/m", agent=None, session_id=None)
    assert exc.value.auth_ref == "omniroute_api_key"


async def test_denied_combo_is_policy_error_with_zero_network(otel: Any) -> None:
    gw = await _started(_settings(omniroute_denied_combos=["claude-sub-*"]))
    gw._http = httpx.AsyncClient(  # any I/O from here on fails the test
        transport=httpx.MockTransport(lambda r: pytest.fail(f"network I/O: {r.url}"))
    )
    with pytest.raises(GatewayPolicyError) as exc:
        gw.resolve("gw:omniroute/Claude-Sub-Pro", agent="coder", session_id=None)
    assert exc.value.reason == "omniroute_denied_combo"
    assert exc.value.agent == "coder"
    assert _denials(otel) == ["omniroute_denied_combo"]


@pytest.mark.parametrize(
    ("attestation", "reason"),
    [
        (None, "omniroute_attestation_missing"),
        ("subscription_oauth", "omniroute_subscription_upstreams"),
    ],
)
async def test_resolve_after_attestation_refusal_keeps_reason(
    attestation: str | None, reason: str, otel: Any
) -> None:
    gw = _gw(
        _settings(omniroute_upstream_attestation=attestation),
        http=_probe(json={"version": "3.8.51"}),
    )
    with pytest.raises(GatewayPolicyError):
        await gw.astartup_check()
    with pytest.raises(GatewayPolicyError) as exc:
        gw.resolve("gw:omniroute/m", agent=None, session_id=None)
    assert exc.value.reason == reason
    assert _denials(otel) == [reason, reason]


def test_build_from_settings() -> None:
    gws = build_from_settings(_settings(), org_id="acme", secret_resolver=_key, ledger=None)
    assert set(gws) == {"omniroute"}
    assert isinstance(gws["omniroute"], OmniRouteGateway)


async def test_secret_never_in_repr() -> None:
    gw = await _started()
    assert SENTINEL not in repr(gw)
    resolved = gw.resolve("gw:omniroute/m", agent=None, session_id=None)
    assert SENTINEL not in repr(resolved)


# ── GW4-03: version probe ────────────────────────────────────────────────────


def _probe(
    *,
    json: Any = None,
    headers: dict[str, str] | None = None,
    status: int = 200,
    seen: list[httpx.Request] | None = None,
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=json if json is not None else {}, headers=headers)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _failing_probe() -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_detect_version_reads_json_from_health_root() -> None:
    seen: list[httpx.Request] = []
    gw = _gw(http=_probe(json={"version": "3.8.52", "providers": 4}, seen=seen))
    assert await gw.detect_version() == "3.8.52"
    assert str(seen[0].url) == "http://localhost:20128/api/monitoring/health"
    assert seen[0].headers["authorization"] == f"Bearer {SENTINEL}"


async def test_detect_version_reads_nested_data_version() -> None:
    assert await _gw(http=_probe(json={"data": {"version": "3.9.0"}})).detect_version() == "3.9.0"


async def test_detect_version_reads_header_and_strips_v() -> None:
    gw = _gw(http=_probe(json={"providers": 2}, headers={"x-omniroute-version": "v3.8.52"}))
    assert await gw.detect_version() == "3.8.52"


async def test_detect_version_rejects_non_semver() -> None:
    assert await _gw(http=_probe(json={"version": "latest"})).detect_version() is None


async def test_detect_version_falls_back_to_declared() -> None:
    gw = _gw(_settings(omniroute_declared_version="3.8.60"), http=_probe(json={"providers": 1}))
    assert await gw.detect_version() == "3.8.60"


async def test_detect_version_network_error_is_fail_open(otel: Any) -> None:
    assert await _gw(http=_failing_probe()).detect_version() is None
    assert {"gateway": "omniroute", "kind": "version_probe"} in otel.counts["gateway_errors"]


async def test_detect_version_http_error_falls_back_to_declared() -> None:
    gw = _gw(_settings(omniroute_declared_version="3.8.51"), http=_probe(status=503))
    assert await gw.detect_version() == "3.8.51"


# ── GW4-03/04: startup gate ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("version", "ok"),
    [("3.8.48", False), ("3.8.50", False), ("3.8.51", True), ("3.9.0", True), ("4.0.0", True)],
)
async def test_version_floor(version: str, ok: bool, otel: Any) -> None:
    gw = _gw(http=_probe(json={"version": version}))
    if ok:
        await gw.astartup_check()
        assert _denials(otel) == []
    else:
        with pytest.raises(GatewayPolicyError) as exc:
            await gw.astartup_check()
        assert exc.value.reason == "omniroute_vulnerable_version"
        assert _denials(otel) == ["omniroute_vulnerable_version"]


async def test_v_prefixed_header_accepted() -> None:
    await _gw(http=_probe(headers={"x-omniroute-version": "v3.8.52"})).astartup_check()


async def test_unknown_version_refused(otel: Any) -> None:
    with pytest.raises(GatewayPolicyError) as exc:
        await _gw(http=_probe(json={"providers": 3})).astartup_check()
    assert exc.value.reason == "omniroute_unknown_version"
    assert _denials(otel) == ["omniroute_unknown_version"]


async def test_unknown_version_allowed_by_flag(otel: Any) -> None:
    gw = _gw(_settings(omniroute_allow_unknown_version=True), http=_failing_probe())
    await gw.astartup_check()
    assert _denials(otel) == []


async def test_declared_version_used_and_logged(otel: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        "prismal.providers.gateways.omniroute.logger.warning",
        lambda event, **kw: events.append(event),
    )
    gw = _gw(_settings(omniroute_declared_version="3.8.55"), http=_probe(json={}))
    await gw.astartup_check()
    assert "gateway.omniroute_version_declared" in events


async def test_declared_version_below_floor_refused() -> None:
    gw = _gw(_settings(omniroute_declared_version="3.8.50"), http=_probe(json={}))
    with pytest.raises(GatewayPolicyError, match="omniroute_vulnerable_version"):
        await gw.astartup_check()


async def test_min_version_is_configurable() -> None:
    gw = _gw(_settings(omniroute_min_version="3.9.0"), http=_probe(json={"version": "3.8.99"}))
    with pytest.raises(GatewayPolicyError, match="omniroute_vulnerable_version"):
        await gw.astartup_check()


async def test_startup_missing_key_raises_credential_error(no_network: Any) -> None:
    guard, client = no_network
    gw = _gw(http=client, resolver=lambda ref: None)
    with pytest.raises(GatewayCredentialError):
        await gw.astartup_check()
    assert guard.requests == []


@pytest.mark.parametrize(
    ("attestation", "allow", "reason"),
    [
        (None, False, "omniroute_attestation_missing"),
        (None, True, "omniroute_attestation_missing"),
        ("subscription_oauth", False, "omniroute_subscription_upstreams"),
        ("subscription_oauth", True, None),
        ("api_keys_only", False, None),
        ("api_keys_only", True, None),
    ],
)
async def test_attestation_matrix(
    attestation: str | None, allow: bool, reason: str | None, otel: Any, no_network: Any
) -> None:
    guard, _ = no_network
    seen: list[httpx.Request] = []
    gw = _gw(
        _settings(
            omniroute_upstream_attestation=attestation,
            omniroute_allow_subscription_upstreams=allow,
        ),
        http=_probe(json={"version": "3.8.51"}, seen=seen),
    )
    if reason is None:
        await gw.astartup_check()
        assert _denials(otel) == []
    else:
        with pytest.raises(GatewayPolicyError) as exc:
            await gw.astartup_check()
        assert exc.value.reason == reason
        assert _denials(otel) == [reason]
        assert seen == []  # refused before any network I/O


async def test_subscription_oauth_logs_warning_every_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        "prismal.providers.gateways.omniroute.logger.warning",
        lambda event, **kw: events.append(event),
    )
    gw = _gw(
        _settings(
            omniroute_upstream_attestation="subscription_oauth",
            omniroute_allow_subscription_upstreams=True,
        ),
        http=_probe(json={"version": "3.8.51"}),
    )
    await gw.astartup_check()
    await gw.astartup_check()
    assert events.count("gateway.omniroute_subscription_upstreams") == 2


async def test_failed_startup_makes_resolve_fail_closed(otel: Any) -> None:
    gw = _gw(http=_probe(json={"version": "3.8.50"}))
    with pytest.raises(GatewayPolicyError):
        await gw.astartup_check()
    with pytest.raises(GatewayPolicyError) as exc:
        gw.resolve("gw:omniroute/m", agent="coder", session_id=None)
    assert exc.value.reason == "omniroute_vulnerable_version"
    assert _denials(otel) == ["omniroute_vulnerable_version"] * 2


async def test_passed_startup_allows_resolve() -> None:
    gw = _gw(http=_probe(json={"version": "3.8.51"}))
    await gw.astartup_check()
    assert gw.resolve("gw:omniroute/m", agent=None, session_id=None).upstream_model == "m"


async def test_secret_never_in_exception_text() -> None:
    gw = _gw(http=_probe(json={"version": "3.8.50"}))
    with pytest.raises(GatewayPolicyError) as exc:
        await gw.astartup_check()
    assert SENTINEL not in str(exc.value)
    assert SENTINEL not in repr(exc.value)


async def test_router_disables_vulnerable_omniroute() -> None:
    s = _settings()
    gw = _gw(s, http=_probe(json={"version": "3.8.50"}))
    router = GatewayRouter({"omniroute": gw}, settings=s)
    await router.astartup()
    assert router.disabled == {"omniroute": "omniroute_vulnerable_version"}
    with pytest.raises(GatewayPolicyError):
        router.resolve("gw:omniroute/m", agent=None, session_id=None)


# ── fail closed until a successful startup (security review) ─────────────────


def test_resolve_before_astartup_is_denied(no_network: Any, otel: Any) -> None:
    guard, client = no_network
    gw = _gw(http=client)
    with pytest.raises(GatewayPolicyError) as exc:
        gw.resolve("gw:omniroute/m", agent="coder", session_id=None)
    assert exc.value.reason == "omniroute_unverified_version"
    assert exc.value.agent == "coder"
    assert guard.requests == []
    assert _denials(otel) == ["omniroute_unverified_version"]


async def test_resolve_after_successful_astartup_works(otel: Any) -> None:
    gw = await _started()
    resolved = gw.resolve("gw:omniroute/m", agent=None, session_id=None)
    assert resolved.litellm_model == "openai/m"
    assert _denials(otel) == []


# ── GW4-05: public plain-HTTP warning (warn only) ────────────────────────────


@pytest.mark.parametrize(
    "base_url",
    [
        "http://omniroute.example.com/v1",
        "http://8.8.8.8:20128/v1",
        "http://[2001:4860::8888]:20128/v1",
    ],
)
async def test_public_http_warns_once_and_counts(
    base_url: str, otel: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        "prismal.providers.gateways.omniroute.logger.warning",
        lambda event, **kw: events.append(event),
    )
    gw = _gw(_settings(omniroute_base_url=base_url), http=_probe(json={"version": "3.8.51"}))
    await gw.astartup_check()  # warn only, never blocks
    await gw.astartup_check()
    assert _denials(otel) == ["omniroute_public_http"]
    assert events.count("gateway.omniroute_public_http") == 1
    gw.resolve("gw:omniroute/m", agent=None, session_id=None)


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:20128/v1",
        "http://127.0.0.1:20128/v1",
        "http://[::1]:20128/v1",
        "http://10.1.2.3:20128/v1",
        "http://192.168.1.10:20128/v1",
        "http://172.16.0.4:20128/v1",
        "http://omniroute.local/v1",
        "http://api.localhost/v1",
        "http://omni.internal/v1",
        "https://omniroute.example.com/v1",
    ],
)
async def test_private_or_tls_base_url_not_counted(base_url: str, otel: Any) -> None:
    gw = _gw(_settings(omniroute_base_url=base_url), http=_probe(json={"version": "3.8.51"}))
    await gw.astartup_check()
    assert _denials(otel) == []
