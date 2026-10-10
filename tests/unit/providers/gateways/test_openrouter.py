"""OpenRouter gateway adapter (Phase GW2 — SPEC-GW-OR-001)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from langchain_core.messages import HumanMessage
from litellm import ModelResponse
from pydantic import SecretStr

from prismal.core.exceptions import GatewayCredentialError, GatewayPolicyError
from prismal.providers.gateways.base import (
    GatewayPort,
    GenerationStats,
    QuotaSnapshot,
    WireProtocol,
    session_hash,
)
from prismal.providers.gateways.openrouter import OpenRouterGateway, build_from_settings
from prismal.providers.gateways.router import GatewayRouter
from prismal.providers.registry import ProviderRegistry

from .conftest import gw_settings

SENTINEL = "sk-or-SENTINEL-gw2"
REF = "gw:openrouter/anthropic/claude-sonnet-4.5"


def _resolver(ref: str) -> SecretStr | None:
    return SecretStr(SENTINEL) if ref == "openrouter_api_key" else None


def _gw(http: Any = None, org_id: str | None = "acme", **settings_kw: Any) -> OpenRouterGateway:
    settings_kw.setdefault("openrouter_enabled", True)
    return OpenRouterGateway(
        settings=gw_settings(**settings_kw), secret_resolver=_resolver, org_id=org_id, http=http
    )


# ── GW2-02 resolve ───────────────────────────────────────────────────────────


def test_is_gateway_port_with_openrouter_profile() -> None:
    gw = _gw()
    assert isinstance(gw, GatewayPort)
    assert gw.profile.name == "openrouter"
    assert gw.profile.base_url == "https://openrouter.ai/api/v1"
    assert gw.profile.auth_ref == "openrouter_api_key"


def test_capabilities() -> None:
    caps = _gw().profile.capabilities
    assert caps.reports_cost and caps.has_generation_api and caps.has_quota_api
    assert caps.supports_prompt_cache and caps.enforces_data_policy
    assert caps.owns_model_fallback is False


def test_resolve_model_and_target() -> None:
    r = _gw().resolve(REF, agent="coder", session_id=None)
    assert r.litellm_model == "openrouter/anthropic/claude-sonnet-4.5"
    assert r.upstream_model == "anthropic/claude-sonnet-4.5"
    assert r.api_base == "https://openrouter.ai/api/v1"
    assert r.api_key.get_secret_value() == SENTINEL
    assert r.wire_protocol is WireProtocol.OPENAI_CHAT
    assert r.gateway == "openrouter"
    assert r.cost_mode == "metered"
    assert r.owns_model_fallback is False


def test_resolve_keeps_allowed_variant() -> None:
    r = _gw().resolve(REF + ":nitro", agent=None, session_id=None)
    assert r.litellm_model == "openrouter/anthropic/claude-sonnet-4.5:nitro"
    assert r.upstream_model == "anthropic/claude-sonnet-4.5:nitro"


def test_resolve_custom_base_url() -> None:
    r = _gw(openrouter_base_url="https://or.example/api/v1").resolve(
        REF, agent=None, session_id=None
    )
    assert r.api_base == "https://or.example/api/v1"


def test_attribution_headers_default() -> None:
    headers = dict(_gw().resolve(REF, agent=None, session_id=None).extra_headers)
    assert headers == {"X-OpenRouter-Title": "prismal"}
    assert "X-Title" not in headers


def test_attribution_headers_full() -> None:
    headers = (
        _gw(
            openrouter_app_url="https://app.example",
            openrouter_app_title="acme-bot",
            openrouter_app_categories=["coding", "agents"],
        )
        .resolve(REF, agent=None, session_id=None)
        .extra_headers
    )
    assert headers["HTTP-Referer"] == "https://app.example"
    assert headers["X-OpenRouter-Title"] == "acme-bot"
    assert headers["X-OpenRouter-Categories"] == "coding,agents"
    assert "X-Title" not in headers


def test_sticky_session_is_hashed() -> None:
    r = _gw().resolve(REF, agent=None, session_id="raw-session-42")
    assert r.extra_body["session_id"] == session_hash("acme", "raw-session-42", 64)
    assert len(r.extra_body["session_id"]) == 64
    assert "raw-session-42" not in repr(r)


def test_session_hash_is_tenant_scoped() -> None:
    a = _gw(org_id="a").resolve(REF, agent=None, session_id="s").extra_body["session_id"]
    b = _gw(org_id="b").resolve(REF, agent=None, session_id="s").extra_body["session_id"]
    assert a != b


def test_no_session_id_when_absent_or_disabled() -> None:
    assert "session_id" not in _gw().resolve(REF, agent=None, session_id=None).extra_body
    off = _gw(openrouter_session_stickiness=False).resolve(REF, agent=None, session_id="s")
    assert "session_id" not in off.extra_body


def test_resolve_does_no_network_io(no_network: Any) -> None:
    guard, client = no_network
    _gw(http=client).resolve(REF, agent="coder", session_id="s")
    assert guard.requests == []


def test_secret_never_in_repr() -> None:
    r = _gw().resolve(REF, agent=None, session_id="s")
    assert SENTINEL not in repr(r)
    assert SENTINEL not in repr(_gw())


def test_missing_key_raises_credential_error_without_secret() -> None:
    gw = OpenRouterGateway(settings=gw_settings(), secret_resolver=lambda ref: None)
    with pytest.raises(GatewayCredentialError) as exc:
        gw.resolve(REF, agent=None, session_id=None)
    assert SENTINEL not in str(exc.value)


def test_build_from_settings() -> None:
    gateways = build_from_settings(
        gw_settings(openrouter_enabled=True), org_id="acme", secret_resolver=_resolver, ledger=None
    )
    assert list(gateways) == ["openrouter"]
    assert isinstance(gateways["openrouter"], OpenRouterGateway)
    assert gateways["openrouter"].resolve(REF, agent=None, session_id="s").extra_body[
        "session_id"
    ] == session_hash("acme", "s", 64)


# ── GW2-03 provider object ───────────────────────────────────────────────────

PRIVACY = {"data_collection": "deny", "zdr": True}


def test_privacy_defaults_in_extra_body() -> None:
    r = _gw().resolve(REF, agent=None, session_id=None)
    assert r.extra_body["provider"] == PRIVACY


def test_provider_object_require_parameters_when_tools_or_schema() -> None:
    gw = _gw()
    assert gw.build_provider_object(wants_tools=False, wants_structured=False) == PRIVACY
    for kw in ({"wants_tools": True}, {"wants_structured": True}):
        flags = {"wants_tools": False, "wants_structured": False, **kw}
        assert gw.build_provider_object(**flags) == {**PRIVACY, "require_parameters": True}
    r = gw.resolve(REF, agent=None, session_id=None, wants_tools=True)
    assert r.extra_body["provider"]["require_parameters"] is True


def test_provider_object_copies_routing_constraints_verbatim() -> None:
    provider = _gw(
        openrouter_provider_sort="price",
        openrouter_max_price={"prompt": 1.0, "completion": 2.5},
        openrouter_only=["anthropic", "google-vertex"],
        openrouter_ignore=["deepinfra"],
    ).build_provider_object(wants_tools=False, wants_structured=False)
    assert provider == {
        **PRIVACY,
        "sort": "price",
        "max_price": {"prompt": 1.0, "completion": 2.5},
        "only": ["anthropic", "google-vertex"],
        "ignore": ["deepinfra"],
    }


def test_provider_omitted_when_empty() -> None:
    gw = _gw(openrouter_require_zdr=False)
    assert gw.build_provider_object(wants_tools=False, wants_structured=False) == {}
    assert "provider" not in gw.resolve(REF, agent=None, session_id=None).extra_body
    assert gw.build_provider_object(wants_tools=True, wants_structured=False) == {
        "require_parameters": True
    }


def test_provider_and_plugins_never_in_headers() -> None:
    r = _gw(openrouter_plugins=["pdf"]).resolve(REF, agent=None, session_id=None)
    assert not {"provider", "plugins"} & {k.lower() for k in r.extra_headers}


# ── GW2-04 gateway-owned model fallback ──────────────────────────────────────


def test_no_models_without_fallbacks() -> None:
    assert "models" not in _gw().resolve(REF, agent=None, session_id=None).extra_body


def test_fallback_models_owns_model_fallback() -> None:
    gw = _gw(openrouter_fallback_models=["openai/gpt-5.1", "google/gemini-3-pro"])
    assert gw.profile.capabilities.owns_model_fallback is True
    r = gw.resolve(REF + ":nitro", agent=None, session_id=None)
    assert r.owns_model_fallback is True
    assert r.extra_body["models"] == [
        "anthropic/claude-sonnet-4.5:nitro",
        "openai/gpt-5.1",
        "google/gemini-3-pro",
    ]


# ── GW2-05 variant allow-list + auto router gate ─────────────────────────────


def test_disallowed_variant_denied_before_network(no_network: Any, otel: Any) -> None:
    guard, client = no_network
    with pytest.raises(GatewayPolicyError, match="variant_not_allowed") as exc:
        _gw(http=client).resolve(REF + ":free", agent="coder", session_id="s")
    assert guard.requests == []
    assert SENTINEL not in str(exc.value)
    assert otel.counts["gateway_policy_denials"] == [
        {"gateway": "openrouter", "reason": "variant_not_allowed"}
    ]


def test_opted_in_variant_allowed() -> None:
    r = _gw(openrouter_allowed_variants=["free"]).resolve(
        REF + ":free", agent=None, session_id=None
    )
    assert r.upstream_model.endswith(":free")


def test_variant_checked_before_credentials() -> None:
    gw = OpenRouterGateway(settings=gw_settings(), secret_resolver=lambda ref: None)
    with pytest.raises(GatewayPolicyError, match="variant_not_allowed"):
        gw.resolve(REF + ":batch", agent=None, session_id=None)


def test_auto_router_disabled_by_default(otel: Any) -> None:
    with pytest.raises(GatewayPolicyError, match="auto_router_disabled"):
        _gw().resolve("gw:openrouter/openrouter/auto", agent="coder", session_id=None)
    assert otel.counts["gateway_policy_denials"][0]["reason"] == "auto_router_disabled"


def test_auto_router_allowed_when_opted_in() -> None:
    r = _gw(openrouter_allow_auto=True).resolve(
        "gw:openrouter/openrouter/auto", agent=None, session_id=None
    )
    assert r.litellm_model == "openrouter/openrouter/auto"
    assert "plugins" not in r.extra_body


# ── GW2-10 plugins + auto-router constraints ─────────────────────────────────


def test_plugins_passed_in_extra_body() -> None:
    r = _gw(openrouter_plugins=["response-healing", "pdf"]).resolve(
        REF, agent=None, session_id=None
    )
    assert r.extra_body["plugins"] == [{"id": "response-healing"}, {"id": "pdf"}]


def test_no_plugins_by_default() -> None:
    assert "plugins" not in _gw().resolve(REF, agent=None, session_id=None).extra_body


def test_auto_router_plugin_carries_allowed_models_and_tier() -> None:
    r = _gw(
        openrouter_allow_auto=True,
        openrouter_auto_allowed_models=["anthropic/*", "openai/gpt-5.1"],
        openrouter_auto_tier="medium",
        openrouter_plugins=["pdf"],
    ).resolve("gw:openrouter/openrouter/auto", agent=None, session_id=None)
    assert r.extra_body["plugins"] == [
        {"id": "pdf"},
        {
            "id": "auto-router",
            "allowed_models": ["anthropic/*", "openai/gpt-5.1"],
            "cost_tier": "medium",
        },
    ]


def test_auto_router_plugin_not_sent_for_regular_models() -> None:
    r = _gw(
        openrouter_allow_auto=True,
        openrouter_auto_allowed_models=["anthropic/*"],
        openrouter_auto_tier="low",
    ).resolve(REF, agent=None, session_id=None)
    assert "plugins" not in r.extra_body


# ── GW2-06 reported cost ─────────────────────────────────────────────────────


def test_reported_cost_byok_upstream() -> None:
    cost = _gw().reported_cost(
        {
            "id": "gen-9",
            "usage": {"cost": 0.0, "cost_details": {"upstream_inference_cost": 0.0123}},
        }
    )
    assert cost is not None
    assert cost.cost_usd == 0.0
    assert cost.upstream_cost_usd == 0.0123
    assert cost.generation_id == "gen-9"


def test_reported_cost_none_never_raises() -> None:
    assert _gw().reported_cost(object()) is None
    assert _gw().reported_cost({"usage": {}}) is None


# ── GW2-07 quota + generation lookup ─────────────────────────────────────────

KEY_JSON: dict[str, Any] = {
    "data": {
        "label": "sk-or-v1-abc...",
        "limit": 100.0,
        "limit_remaining": 74.5,
        "usage": 25.5,
        "usage_daily": 1.25,
        "usage_weekly": 6.0,
        "usage_monthly": 25.5,
        "is_free_tier": False,
        "free_model_daily_requests": {"used": 12, "limit": 1000, "remaining": 988},
    }
}


class Recorder:
    def __init__(self, status: int = 200, payload: Any = None, exc: Exception | None = None):
        self.status, self.payload, self.exc = status, payload, exc
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.exc is not None:
            raise self.exc
        return httpx.Response(self.status, json=self.payload)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


async def test_quota_reads_key_endpoint_with_bearer_header() -> None:
    rec = Recorder(payload=KEY_JSON)
    snap = await _gw(http=rec.client()).quota()
    assert snap == QuotaSnapshot(
        gateway="openrouter",
        window="credit",
        unit="usd",
        limit=100.0,
        used=25.5,
        remaining=74.5,
        resets_at=None,
        source="api",
    )
    (req,) = rec.requests
    assert req.method == "GET"
    assert str(req.url) == "https://openrouter.ai/api/v1/key"
    assert req.headers["Authorization"] == f"Bearer {SENTINEL}"
    assert SENTINEL not in str(req.url)


async def test_quota_unlimited_key() -> None:
    payload = {"data": {"limit": None, "limit_remaining": None, "usage": 3.0}}
    snap = await _gw(http=Recorder(payload=payload).client()).quota()
    assert snap is not None and snap.limit is None and snap.remaining is None and snap.used == 3.0


async def test_quotas_include_windows_and_free_requests() -> None:
    snaps = await _gw(http=Recorder(payload=KEY_JSON).client()).quotas()
    by_window = {(s.window, s.unit): s for s in snaps}
    assert by_window[("credit", "usd")].remaining == 74.5
    assert by_window[("day", "usd")].used == 1.25
    assert by_window[("week", "usd")].used == 6.0
    assert by_window[("month", "usd")].used == 25.5
    for window in ("day", "week", "month"):
        assert by_window[(window, "usd")].limit is None
        assert by_window[(window, "usd")].remaining is None
    free = by_window[("day", "requests")]
    assert (free.used, free.limit, free.remaining) == (12, 1000, 988)
    assert all(s.source == "api" and s.gateway == "openrouter" for s in snaps)


async def test_quotas_without_optional_fields() -> None:
    payload = {"data": {"limit": 5.0, "limit_remaining": 5.0, "usage": 0.0}}
    snaps = await _gw(http=Recorder(payload=payload).client()).quotas()
    assert [(s.window, s.unit) for s in snaps] == [("credit", "usd")]


@pytest.mark.parametrize(
    "rec",
    [
        Recorder(status=500, payload={"error": "boom"}),
        Recorder(status=200, payload={"nope": 1}),
        Recorder(status=200, payload={"data": {"usage": "abc"}}),
        Recorder(exc=httpx.ConnectError("down")),
    ],
)
async def test_quota_fail_open(rec: Recorder, otel: Any) -> None:
    gw = _gw(http=rec.client())
    assert await gw.quota() is None
    assert await gw.quotas() == []
    assert otel.counts["gateway_errors"][0] == {"gateway": "openrouter", "kind": "quota_lookup"}


async def test_quota_missing_key_fail_open(otel: Any) -> None:
    gw = OpenRouterGateway(
        settings=gw_settings(), secret_resolver=lambda ref: None, http=Recorder().client()
    )
    assert await gw.quota() is None


GEN_JSON: dict[str, Any] = {
    "data": {
        "id": "gen-123",
        "model": "anthropic/claude-sonnet-4.5",
        "provider_name": "Anthropic",
        "total_cost": 0.0042,
        "tokens_prompt": 100,
        "tokens_completion": 20,
        "native_tokens_prompt": 110,
        "native_tokens_completion": 22,
        "native_tokens_cached": 64,
        "native_tokens_reasoning": 5,
    }
}


async def test_fetch_generation() -> None:
    rec = Recorder(payload=GEN_JSON)
    stats = await _gw(http=rec.client()).fetch_generation("gen-123")
    assert stats == GenerationStats(
        generation_id="gen-123",
        total_cost_usd=0.0042,
        served_model="anthropic/claude-sonnet-4.5",
        provider_name="Anthropic",
        prompt_tokens=110,
        completion_tokens=22,
        cached_tokens=64,
        reasoning_tokens=5,
    )
    (req,) = rec.requests
    assert req.url.path == "/api/v1/generation"
    assert req.url.params["id"] == "gen-123"
    assert req.headers["Authorization"] == f"Bearer {SENTINEL}"


async def test_fetch_generation_normalized_token_fallback() -> None:
    payload = {
        "data": {
            "model": "openai/gpt-5.1",
            "total_cost": 0.001,
            "tokens_prompt": 7,
            "tokens_completion": 3,
        }
    }
    stats = await _gw(http=Recorder(payload=payload).client()).fetch_generation("gen-x")
    assert stats is not None
    assert (stats.prompt_tokens, stats.completion_tokens) == (7, 3)
    assert (stats.cached_tokens, stats.reasoning_tokens) == (0, 0)
    assert stats.provider_name is None
    assert stats.generation_id == "gen-x"


@pytest.mark.parametrize(
    "rec",
    [
        Recorder(status=404, payload={"error": "not found"}),
        Recorder(status=200, payload={"data": {"model": "m"}}),
        Recorder(exc=httpx.ReadTimeout("slow")),
    ],
)
async def test_fetch_generation_fail_open(rec: Recorder, otel: Any) -> None:
    assert await _gw(http=rec.client()).fetch_generation("gen-1") is None
    assert otel.counts["gateway_errors"] == [{"gateway": "openrouter", "kind": "generation_lookup"}]


# ── GW2-09 request capture (registry → GatewayChatLiteLLM → LiteLLM kwargs) ──


RAW_SESSION = "raw-session-7f3a"


class CaptureClient:
    """Stands in for the ``litellm`` module; records every completion call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def completion(self, **kwargs: Any) -> ModelResponse:
        self.calls.append(kwargs)
        return ModelResponse(
            id="gen-cap",
            model="anthropic/claude-sonnet-4.5",
            choices=[
                {
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                    "index": 0,
                }
            ],
            usage={"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4, "cost": 0.001},
        )


def _captured(
    model: str = REF, session_id: str | None = RAW_SESSION, calls: int = 1, **settings_kw: Any
) -> list[dict[str, Any]]:
    settings_kw.setdefault("openrouter_enabled", True)
    settings_kw.setdefault("openrouter_app_url", "https://app.example")
    s = gw_settings(**settings_kw)
    gw = OpenRouterGateway(settings=s, secret_resolver=_resolver, org_id="acme")
    registry = ProviderRegistry(
        settings=s, gateway_router=GatewayRouter({"openrouter": gw}, settings=s)
    )
    client = CaptureClient()
    for _ in range(calls):
        llm = registry.get_llm(model, agent="coder", session_id=session_id)
        llm.client = client  # type: ignore[attr-defined]
        llm.invoke([HumanMessage(content="hi")])
    return client.calls


def test_captured_request_shape() -> None:
    (call,) = _captured()
    assert call["model"] == "openrouter/anthropic/claude-sonnet-4.5"
    assert call["api_base"] == "https://openrouter.ai/api/v1"
    assert call["api_key"] == SENTINEL
    assert call["extra_headers"] == {
        "HTTP-Referer": "https://app.example",
        "X-OpenRouter-Title": "prismal",
    }
    body = call["extra_body"]
    assert body["provider"] == {"data_collection": "deny", "zdr": True}
    assert body["session_id"] == session_hash("acme", RAW_SESSION, 64)
    assert RAW_SESSION not in repr({k: v for k, v in call.items() if k != "messages"})
    assert "models" not in body


def test_captured_models_fallback() -> None:
    (call,) = _captured(openrouter_fallback_models=["openai/gpt-5.1"])
    assert call["extra_body"]["models"] == ["anthropic/claude-sonnet-4.5", "openai/gpt-5.1"]
    assert call["extra_body"]["provider"] == {"data_collection": "deny", "zdr": True}


def test_every_captured_request_carries_privacy_defaults() -> None:
    calls = [
        *_captured(calls=3),
        *_captured(model=REF + ":floor", session_id=None),
        *_captured(openrouter_plugins=["response-healing"], openrouter_provider_sort="latency"),
        *_captured(model="gw:openrouter/openrouter/auto", openrouter_allow_auto=True),
    ]
    assert len(calls) == 6
    for call in calls:
        provider = call["extra_body"]["provider"]
        assert provider["data_collection"] == "deny"
        assert provider["zdr"] is True
        assert "X-Title" not in call["extra_headers"]


@pytest.mark.parametrize(
    ("model", "kw", "settings_kw"),
    [
        (REF, {}, {}),
        (REF + ":floor", {"session_id": "s"}, {}),
        (REF + ":nitro", {"wants_tools": True}, {}),
        (REF, {"wants_structured": True}, {"openrouter_provider_sort": "price"}),
        (REF, {}, {"openrouter_fallback_models": ["openai/gpt-5.1"]}),
        (REF, {}, {"openrouter_only": ["anthropic"], "openrouter_plugins": ["pdf"]}),
        ("gw:openrouter/openrouter/auto", {}, {"openrouter_allow_auto": True}),
    ],
)
def test_every_resolve_carries_privacy_defaults(
    model: str, kw: dict[str, Any], settings_kw: dict[str, Any]
) -> None:
    kw.setdefault("session_id", None)
    provider = _gw(**settings_kw).resolve(model, agent="coder", **kw).extra_body["provider"]
    assert provider["data_collection"] == "deny"
    assert provider["zdr"] is True
