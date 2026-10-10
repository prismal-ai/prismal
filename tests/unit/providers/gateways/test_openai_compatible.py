"""Generic OpenAI-compatible adapter (Phase GW — SPEC-GW-GEN-001)."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from prismal.core.exceptions import GatewayConfigError, GatewayCredentialError, GatewayPolicyError
from prismal.providers.gateways.base import (
    GatewayCallMeta,
    GatewayCapabilities,
    GatewayPort,
    GatewayProfile,
    WireProtocol,
    session_hash,
)
from prismal.providers.gateways.openai_compatible import (
    OpenAICompatibleGateway,
    build_openai_compatible_gateways,
)
from prismal.providers.gateways.policy import GatewayDataPolicy

from .conftest import gw_settings

SENTINEL = "sk-SENTINEL-generic"


def _gw(**profile_kw: object) -> OpenAICompatibleGateway:
    base: dict[str, object] = {
        "name": "my-proxy",
        "base_url": "https://proxy.local/v1",
        "auth_ref": "my_proxy_key",
        "default_headers": {"X-Team": "core"},
    }
    base.update(profile_kw)
    profile = GatewayProfile(**base)  # type: ignore[arg-type]
    return OpenAICompatibleGateway(
        profile, secret_resolver=lambda ref: SecretStr(SENTINEL) if ref == "my_proxy_key" else None
    )


def test_satisfies_port() -> None:
    assert isinstance(_gw(), GatewayPort)


def test_resolve_produces_openai_kwargs() -> None:
    r = _gw().resolve("gw:my-proxy/qwen3-coder", agent="coder", session_id="s1")
    assert r.litellm_model == "openai/qwen3-coder"
    assert r.api_base == "https://proxy.local/v1"
    assert dict(r.extra_headers) == {"X-Team": "core"}
    assert dict(r.extra_body) == {}
    assert r.wire_protocol is WireProtocol.OPENAI_CHAT
    assert r.gateway == "my-proxy"
    assert r.upstream_model == "qwen3-coder"
    assert r.api_key.get_secret_value() == SENTINEL
    assert r.owns_model_fallback is False
    assert r.cost_mode == "metered"


def test_resolve_keeps_variant_and_slashes() -> None:
    r = _gw().resolve("gw:my-proxy/org/model:fast", agent=None, session_id=None)
    assert r.litellm_model == "openai/org/model:fast"
    assert r.upstream_model == "org/model:fast"


def test_owns_model_fallback_from_capabilities() -> None:
    gw = _gw(capabilities=GatewayCapabilities(owns_model_fallback=True))
    assert gw.resolve("gw:my-proxy/m", agent=None, session_id=None).owns_model_fallback


def test_missing_secret_raises_credential_error_naming_ref() -> None:
    gw = OpenAICompatibleGateway(
        GatewayProfile(name="my-proxy", base_url="https://p/v1", auth_ref="my_proxy_key"),
        secret_resolver=lambda ref: None,
    )
    with pytest.raises(GatewayCredentialError) as exc:
        gw.resolve("gw:my-proxy/m", agent=None, session_id=None)
    assert exc.value.auth_ref == "my_proxy_key"


def test_empty_auth_ref_needs_no_key() -> None:
    gw = OpenAICompatibleGateway(
        GatewayProfile(name="local", base_url="http://127.0.0.1:8000/v1", auth_ref=""),
        secret_resolver=lambda ref: None,
    )
    r = gw.resolve("gw:local/m", agent=None, session_id=None)
    assert r.api_key.get_secret_value() == ""


def test_wrong_gateway_name_is_config_error() -> None:
    with pytest.raises(GatewayConfigError):
        _gw().resolve("gw:other/m", agent=None, session_id=None)


def test_non_gw_ref_is_config_error() -> None:
    with pytest.raises(GatewayConfigError):
        _gw().resolve("gpt-4o", agent=None, session_id=None)


def test_denied_model_raises_policy_error_and_counts(otel: type) -> None:
    gw = _gw(data_policy=GatewayDataPolicy(denied_model_patterns=("bad*",)))
    with pytest.raises(GatewayPolicyError) as exc:
        gw.resolve("gw:my-proxy/bad-model", agent="coder", session_id=None)
    assert exc.value.reason == "denied_model"
    assert otel.counts["gateway_policy_denials"] == [
        {"gateway": "my-proxy", "reason": "denied_model"}
    ]


def test_resolver_exception_is_treated_as_missing() -> None:
    def boom(ref: str) -> SecretStr | None:
        raise RuntimeError("vault down")

    gw = OpenAICompatibleGateway(
        GatewayProfile(name="my-proxy", base_url="https://p/v1", auth_ref="k"), secret_resolver=boom
    )
    with pytest.raises(GatewayCredentialError):
        gw.resolve("gw:my-proxy/m", agent=None, session_id=None)


def test_resolve_never_leaks_secret_in_errors() -> None:
    gw = _gw()
    with pytest.raises(GatewayConfigError) as exc:
        gw.resolve("gw:my-proxy/", agent=None, session_id=None)
    assert SENTINEL not in str(exc.value)


async def test_fail_open_async_defaults() -> None:
    gw = _gw()
    assert await gw.quota() is None
    assert await gw.fetch_generation("gen-1") is None
    await gw.astartup_check()
    await gw.aclose()


def test_reported_cost_reads_meta_usage_and_hidden_params() -> None:
    from langchain_core.messages import AIMessage

    gw = _gw()
    meta = GatewayCallMeta(
        gateway="my-proxy",
        requested_model="m",
        served_model="m",
        generation_id="g1",
        reported_cost_usd=0.25,
        cost_mode="metered",
    )
    msg = AIMessage(content="x", response_metadata={"prismal_gateway": meta.to_dict()})
    rc = gw.reported_cost(msg)
    assert rc is not None and rc.cost_usd == 0.25 and rc.generation_id == "g1"
    assert gw.reported_cost({"usage": {"cost": 0.5}, "id": "g2"}).cost_usd == 0.5  # type: ignore[union-attr]

    class Raw:
        _hidden_params = {"response_cost": 0.75}  # noqa: RUF012

        def get(self, key: str, default: object = None) -> object:
            return default

    rc2 = gw.reported_cost(Raw())
    assert rc2 is not None and rc2.cost_usd == 0.75 and rc2.source == "hidden_params"
    assert gw.reported_cost(object()) is None
    assert gw.reported_cost(AIMessage(content="x")) is None


def test_session_hash_is_stable_scoped_and_never_raw() -> None:
    a = session_hash("acme", "sess-1", 64)
    assert a == session_hash("acme", "sess-1", 64)
    assert a != session_hash("other", "sess-1", 64)
    assert "sess-1" not in a and len(a) == 64
    assert len(session_hash(None, "s", 32)) == 32


def test_build_from_settings() -> None:
    s = gw_settings(
        llm_gateway_openai_compatible={
            "my-proxy": {
                "base_url": "https://proxy.local/v1",
                "auth_ref": "my_proxy_key",
                "headers": {"X-Team": "core"},
                "owns_model_fallback": True,
            }
        }
    )
    gws = build_openai_compatible_gateways(s, secret_resolver=lambda ref: SecretStr("k"))
    assert set(gws) == {"my-proxy"}
    gw = gws["my-proxy"]
    assert gw.profile.default_headers == {"X-Team": "core"}
    assert gw.profile.capabilities.owns_model_fallback is True
