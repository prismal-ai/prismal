"""OpenCode Zen / Go adapter (Phase GW — SPEC-GW-OC-001; GW3-02..06, GW3-09)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from prismal.core.exceptions import (
    GatewayConfigError,
    GatewayCredentialError,
    GatewayPolicyError,
)
from prismal.providers.gateways import opencode
from prismal.providers.gateways.base import GatewayPort, ResolvedModel, WireProtocol, session_hash
from prismal.providers.gateways.chat_model import GatewayChatLiteLLM
from prismal.providers.gateways.opencode import (
    OpenCodeGateway,
    load_opencode_model_protocols,
    opencode_profile,
)
from prismal.providers.gateways.quota import QuotaLedger
from prismal.providers.gateways.router import GatewayRouter
from prismal.providers.registry import ProviderRegistry

from .conftest import NoNetwork, gw_settings

SENTINEL = "sk-SENTINEL-opencode"
CHAT, MSG, RESP = (
    WireProtocol.OPENAI_CHAT,
    WireProtocol.ANTHROPIC_MESSAGES,
    WireProtocol.OPENAI_RESPONSES,
)
PROTOCOLS: dict[str, WireProtocol] = {
    "glm-5.3": CHAT,
    "glm-5.2": CHAT,
    "minimax-m3": MSG,
    "gpt-6-luna": RESP,
    "grok-4.7": RESP,
}


def _resolver(ref: str) -> SecretStr | None:
    return SecretStr(SENTINEL) if ref == "opencode_api_key" else None


def _gw(
    plan: str = "go",
    *,
    http: httpx.AsyncClient | None = None,
    ledger: QuotaLedger | None = None,
    protocols: dict[str, WireProtocol] | None = None,
    resolver: Any = _resolver,
    org_id: str | None = "acme",
    **settings_kw: Any,
) -> OpenCodeGateway:
    settings = gw_settings(
        opencode_zen_enabled=plan == "zen", opencode_go_enabled=plan == "go", **settings_kw
    )
    return OpenCodeGateway(
        opencode_profile(settings, plan),  # type: ignore[arg-type]
        plan=plan,  # type: ignore[arg-type]
        protocols=dict(PROTOCOLS if protocols is None else protocols),
        ledger=ledger,
        secret_resolver=resolver,
        settings=settings,
        http=http,
        org_id=org_id,
    )


# ── protocol map (GW3-02) ────────────────────────────────────────────────────


def test_shipped_protocol_map_loads() -> None:
    maps = load_opencode_model_protocols()
    assert set(maps) == {"zen", "go"}
    assert maps["zen"]["claude-sonnet-4-5"] is MSG
    assert maps["zen"]["gpt-5.5"] is RESP
    assert maps["zen"]["glm-5.3"] is CHAT
    assert maps["go"]["minimax-m3"] is MSG
    assert maps["go"]["glm-5.3"] is CHAT
    assert maps["go"]["gpt-6-luna"] is RESP
    every = {**maps["zen"], **maps["go"]}
    assert not [m for m in every if m.startswith("gemini")]  # Google-native: unsupported
    assert all(isinstance(p, WireProtocol) for p in every.values())


def test_default_path_resolves_from_another_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert load_opencode_model_protocols()["go"]["glm-5.3"] is CHAT


def test_custom_path(tmp_path: Path) -> None:
    path = tmp_path / "m.yaml"
    path.write_text("zen:\n  a: openai_chat\ngo:\n  b: anthropic_messages\n", encoding="utf-8")
    assert load_opencode_model_protocols(str(path)) == {"zen": {"a": CHAT}, "go": {"b": MSG}}


def test_missing_plan_section_is_empty(tmp_path: Path) -> None:
    path = tmp_path / "m.yaml"
    path.write_text("zen:\n  a: openai_responses\n", encoding="utf-8")
    assert load_opencode_model_protocols(str(path)) == {"zen": {"a": RESP}, "go": {}}


@pytest.mark.parametrize(
    "content",
    [
        "- zen\n",  # not a mapping
        "zen: [a, b]\n",  # plan not a mapping
        "zen:\n  a: google_native\n",  # unknown protocol
        "zen:\n  a: 3\n",  # non-string protocol
        "other:\n  a: openai_chat\n",  # unknown plan
        "zen: {a: openai_chat\n",  # invalid YAML
    ],
)
def test_bad_shape_raises_config_error(tmp_path: Path, content: str) -> None:
    path = tmp_path / "m.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(GatewayConfigError):
        load_opencode_model_protocols(str(path))


def test_missing_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(GatewayConfigError):
        load_opencode_model_protocols(str(tmp_path / "nope.yaml"))


# ── resolve (GW3-03) ─────────────────────────────────────────────────────────


def test_satisfies_port() -> None:
    assert isinstance(_gw("zen"), GatewayPort)
    assert isinstance(_gw("go"), GatewayPort)


@pytest.mark.parametrize(
    ("model", "litellm_model", "protocol"),
    [
        ("glm-5.3", "openai/glm-5.3", CHAT),
        ("minimax-m3", "anthropic/minimax-m3", MSG),
        ("gpt-6-luna", "openai/responses/gpt-6-luna", RESP),
    ],
)
@pytest.mark.parametrize(
    ("plan", "name", "base"),
    [
        ("zen", "opencode", "https://opencode.ai/zen/v1"),
        ("go", "opencode-go", "https://opencode.ai/zen/go/v1"),
    ],
)
def test_protocol_prefix_table(
    plan: str, name: str, base: str, model: str, litellm_model: str, protocol: WireProtocol
) -> None:
    r = _gw(plan).resolve(f"gw:{name}/{model}", agent="coder", session_id=None)
    assert r.litellm_model == litellm_model
    assert r.wire_protocol is protocol
    assert r.api_base == base
    assert r.gateway == name
    assert r.upstream_model == model
    assert r.api_key.get_secret_value() == SENTINEL
    assert r.cost_mode == ("subscription" if plan == "go" else "metered")


def test_unknown_model_is_config_error_never_guessed() -> None:
    with pytest.raises(GatewayConfigError, match="unknown_opencode_model"):
        _gw("zen").resolve("gw:opencode/brand-new-model", agent="coder", session_id=None)


def test_wrong_gateway_reference_rejected() -> None:
    with pytest.raises(GatewayConfigError):
        _gw("zen").resolve("gw:opencode-go/glm-5.3", agent="coder", session_id=None)


def test_profile_name_must_match_plan() -> None:
    settings = gw_settings(opencode_zen_enabled=True)
    with pytest.raises(GatewayConfigError):
        OpenCodeGateway(
            opencode_profile(settings, "zen"),
            plan="go",
            protocols=PROTOCOLS,
            ledger=None,
            secret_resolver=_resolver,
            settings=settings,
        )


def test_custom_base_urls_from_settings() -> None:
    gw = _gw("go", opencode_go_base_url="https://proxy.example/go/v1")
    r = gw.resolve("gw:opencode-go/glm-5.3", agent="coder", session_id=None)
    assert r.api_base == "https://proxy.example/go/v1"


def test_missing_key_is_credential_error() -> None:
    with pytest.raises(GatewayCredentialError):
        _gw("zen", resolver=lambda ref: None).resolve(
            "gw:opencode/glm-5.3", agent="coder", session_id=None
        )


# ── headers (GW3-04) ─────────────────────────────────────────────────────────


def test_user_agent_and_hashed_session_header() -> None:
    r = _gw("go").resolve("gw:opencode-go/glm-5.3", agent="coder", session_id="sess-raw-42")
    headers = dict(r.extra_headers)
    assert headers["User-Agent"] == f"prismal/{opencode.package_version()}"
    assert headers["x-opencode-session"] == session_hash("acme", "sess-raw-42", 32)
    assert len(headers["x-opencode-session"]) == 32
    assert "sess-raw-42" not in repr(headers)


def test_session_header_stable_and_tenant_scoped() -> None:
    a1 = _gw("zen").resolve("gw:opencode/glm-5.3", agent=None, session_id="s")
    a2 = _gw("zen").resolve("gw:opencode/glm-5.3", agent=None, session_id="s")
    b = _gw("zen", org_id="other").resolve("gw:opencode/glm-5.3", agent=None, session_id="s")
    key = "x-opencode-session"
    assert a1.extra_headers[key] == a2.extra_headers[key] != b.extra_headers[key]


def test_no_session_header_without_session() -> None:
    r = _gw("zen").resolve("gw:opencode/glm-5.3", agent=None, session_id=None)
    assert "x-opencode-session" not in r.extra_headers
    assert "User-Agent" in r.extra_headers


def test_package_version_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.metadata

    def boom(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", boom)
    assert opencode.package_version() == "0"


# ── guards (GW3-05) — fail-closed, zero network ──────────────────────────────


@pytest.mark.parametrize("agent", [None, "researcher", "rag_agent", "skynet"])
def test_go_non_coding_agent_denied_with_zero_requests(
    agent: str | None, no_network: tuple[NoNetwork, httpx.AsyncClient], otel: Any
) -> None:
    guard, client = no_network
    gw = _gw("go", http=client, resolver=lambda ref: None)  # guard runs before the secret
    with pytest.raises(GatewayPolicyError) as info:
        gw.resolve("gw:opencode-go/not-even-known", agent=agent, session_id="s")
    assert info.value.reason == "opencode_go_agent_not_allowed"
    assert guard.requests == []
    assert otel.counts["gateway_policy_denials"] == [
        {"gateway": "opencode-go", "reason": "opencode_go_agent_not_allowed"}
    ]


@pytest.mark.parametrize("agent", ["coder", "codeact_agent", "dev_pipeline", "code_review"])
def test_go_coding_agents_allowed(agent: str) -> None:
    assert _gw("go").resolve("gw:opencode-go/glm-5.3", agent=agent, session_id=None)


def test_go_allowed_agents_configurable() -> None:
    gw = _gw("go", opencode_go_allowed_agents=["researcher"])
    assert gw.resolve("gw:opencode-go/glm-5.3", agent="researcher", session_id=None)
    with pytest.raises(GatewayPolicyError):
        gw.resolve("gw:opencode-go/glm-5.3", agent="coder", session_id=None)


def test_zen_has_no_agent_restriction() -> None:
    assert _gw("zen").resolve("gw:opencode/glm-5.3", agent=None, session_id=None)


@pytest.mark.parametrize(
    ("plan", "ref"),
    [
        ("zen", "gw:opencode/grok-4.7"),
        ("zen", "gw:opencode/Muse-Spark-1.3"),
        ("go", "gw:opencode-go/deepseek-v4-flash"),
        ("go", "gw:opencode-go/deepseek-v4-flash-vision-exp"),
        ("go", "gw:opencode-go/muse-spark-1.3-contributor"),
    ],
)
def test_retention_deny_list_with_zero_requests(
    plan: str, ref: str, no_network: tuple[NoNetwork, httpx.AsyncClient], otel: Any
) -> None:
    guard, client = no_network
    gw = _gw(plan, http=client, resolver=lambda ref: None)
    with pytest.raises(GatewayPolicyError) as info:
        gw.resolve(ref, agent="coder", session_id=None)
    assert info.value.reason == "retention_denied_model"
    assert guard.requests == []
    assert otel.counts["gateway_policy_denials"][0]["reason"] == "retention_denied_model"


def test_deny_list_configurable() -> None:
    gw = _gw("zen", opencode_denied_models=["glm-*"])
    with pytest.raises(GatewayPolicyError):
        gw.resolve("gw:opencode/glm-5.3", agent=None, session_id=None)
    assert gw.resolve("gw:opencode/grok-4.7", agent=None, session_id=None)


# ── downgrade target (GW3-07 support) ────────────────────────────────────────


def _downgrade_gw(target: str, plan: str = "go") -> OpenCodeGateway:
    return _gw(
        plan,
        opencode_go_monthly_cap_usd=10.0,
        opencode_go_quota_action="downgrade",
        opencode_go_downgrade_model=target,
    )


def test_resolved_model_downgrade_defaults_to_none() -> None:
    r = ResolvedModel(litellm_model="openai/x", api_base="b", api_key=SecretStr("k"))
    assert r.downgrade_model is None
    assert (
        _gw("go").resolve("gw:opencode-go/glm-5.3", agent="coder", session_id=None).downgrade_model
        is None
    )


def test_go_downgrade_model_precomputed() -> None:
    gw = _downgrade_gw("gw:opencode-go/minimax-m3")
    r = gw.resolve("gw:opencode-go/glm-5.3", agent="coder", session_id=None)
    assert r.downgrade_model == "anthropic/minimax-m3"


@pytest.mark.parametrize(
    "target",
    ["gw:opencode-go/unknown-id", "gw:opencode/glm-5.2", "gw:opencode-go/grok-4.7", "glm-5.2"],
)
def test_bad_downgrade_target_leaves_none(target: str) -> None:
    r = _downgrade_gw(target).resolve("gw:opencode-go/glm-5.3", agent="coder", session_id=None)
    assert r.downgrade_model is None


def test_zen_never_downgrades() -> None:
    r = _downgrade_gw("gw:opencode-go/glm-5.2", plan="zen").resolve(
        "gw:opencode/glm-5.3", agent=None, session_id=None
    )
    assert r.downgrade_model is None


def test_registry_passes_downgrade_model_and_ledger() -> None:
    settings = gw_settings(
        opencode_go_enabled=True,
        opencode_go_monthly_cap_usd=10.0,
        opencode_go_quota_action="downgrade",
        opencode_go_downgrade_model="gw:opencode-go/glm-5.2",
    )
    ledger = QuotaLedger(settings=settings, org_id="acme")
    gw = OpenCodeGateway(
        opencode_profile(settings, "go"),
        plan="go",
        protocols=PROTOCOLS,
        ledger=ledger,
        secret_resolver=_resolver,
        settings=settings,
        org_id="acme",
    )
    router = GatewayRouter({"opencode-go": gw}, settings=settings, org_id="acme", ledger=ledger)
    llm = ProviderRegistry(settings=settings, gateway_router=router).get_llm(
        "gw:opencode-go/glm-5.3", agent="coder", session_id="s"
    )
    assert isinstance(llm, GatewayChatLiteLLM)
    assert llm.downgrade_model == "openai/glm-5.2"
    assert llm.quota_ledger is ledger
    assert llm.cost_mode == "subscription"


# ── secret hygiene ───────────────────────────────────────────────────────────


def test_secret_never_in_repr_or_errors() -> None:
    gw = _gw("go")
    r = gw.resolve("gw:opencode-go/glm-5.3", agent="coder", session_id="s")
    assert SENTINEL not in repr(r)
    assert SENTINEL not in repr(gw)
    for ref, agent in (
        ("gw:opencode-go/glm-5.3", None),
        ("gw:opencode-go/grok-4.7", "coder"),
        ("gw:opencode-go/nope", "coder"),
    ):
        with pytest.raises(Exception) as info:
            gw.resolve(ref, agent=agent, session_id="s")
        assert SENTINEL not in str(info.value)
        assert SENTINEL not in repr(info.value)


# ── refresh_protocols (GW3-06) ───────────────────────────────────────────────


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_refresh_merges_per_model_protocols() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {
                        "id": "new-chat",
                        "endpoint": "https://opencode.ai/zen/go/v1/chat/completions",
                    },
                    {"id": "opencode-go/new-msg", "protocol": "anthropic_messages"},
                    {"id": "new-resp", "endpoint": "/v1/responses"},
                    {"id": "no-info"},
                ],
            },
        )

    gw = _gw("go", http=_client(handler))
    merged = await gw.refresh_protocols()
    assert merged == {"new-chat": CHAT, "new-msg": MSG, "new-resp": RESP}
    assert str(seen[0].url) == "https://opencode.ai/zen/go/v1/models"
    assert seen[0].headers["Authorization"] == f"Bearer {SENTINEL}"
    r = gw.resolve("gw:opencode-go/new-msg", agent="coder", session_id=None)
    assert r.litellm_model == "anthropic/new-msg"
    assert gw.resolve("gw:opencode-go/glm-5.3", agent="coder", session_id=None)  # kept


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"data": [{"id": "a", "object": "model"}]}),  # no protocol field
        httpx.Response(500, json={"error": "down"}),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json=["unexpected"]),
    ],
)
async def test_refresh_fail_open(response: httpx.Response, otel: Any) -> None:
    gw = _gw("zen", http=_client(lambda request: response))
    assert await gw.refresh_protocols() is None
    assert otel.counts["gateway_errors"] == [{"gateway": "opencode", "kind": "models_refresh"}]
    assert gw.resolve("gw:opencode/glm-5.3", agent=None, session_id=None)


async def test_refresh_fail_open_on_transport_error_and_missing_key(otel: Any) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    assert await _gw("zen", http=_client(boom)).refresh_protocols() is None
    assert await _gw("zen", http=_client(boom), resolver=lambda r: None).refresh_protocols() is None
    assert len(otel.counts["gateway_errors"]) == 2


async def test_startup_refreshes_only_when_enabled(
    no_network: tuple[NoNetwork, httpx.AsyncClient],
) -> None:
    guard, client = no_network
    await _gw("zen", http=client).astartup_check()
    assert guard.requests == []
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"data": []})

    await _gw("zen", http=_client(handler), opencode_refresh_models=True).astartup_check()
    assert len(calls) == 1


# ── quota view + composition ─────────────────────────────────────────────────


async def test_quota_is_ledger_view_for_go_only() -> None:
    settings_kw = {"opencode_go_monthly_cap_usd": 10.0}
    ledger = QuotaLedger(settings=gw_settings(opencode_go_enabled=True, **settings_kw))
    ledger.record("opencode-go", "glm-5.3", equivalent_usd=1.5)
    snap = await _gw("go", ledger=ledger, **settings_kw).quota()
    assert snap is not None
    assert (snap.window, snap.source, snap.used) == ("5h", "ledger", 1.5)
    assert await _gw("zen", ledger=ledger).quota() is None
    assert await _gw("go").quota() is None


def test_build_from_settings_per_plan() -> None:
    ledger = QuotaLedger(settings=gw_settings())
    both = opencode.build_from_settings(
        gw_settings(opencode_zen_enabled=True, opencode_go_enabled=True),
        org_id="acme",
        secret_resolver=_resolver,
        ledger=ledger,
    )
    assert set(both) == {"opencode", "opencode-go"}
    assert both["opencode"].profile.base_url == "https://opencode.ai/zen/v1"
    assert both["opencode-go"].profile.base_url == "https://opencode.ai/zen/go/v1"
    assert both["opencode"].profile.auth_ref == "opencode_api_key"
    r = both["opencode-go"].resolve("gw:opencode-go/glm-5.3", agent="coder", session_id=None)
    assert r.litellm_model == "openai/glm-5.3"
    zen_only = opencode.build_from_settings(
        gw_settings(opencode_zen_enabled=True), org_id=None, secret_resolver=_resolver, ledger=None
    )
    assert set(zen_only) == {"opencode"}
    go_only = opencode.build_from_settings(
        gw_settings(opencode_go_enabled=True), org_id=None, secret_resolver=_resolver, ledger=None
    )
    assert set(go_only) == {"opencode-go"}


def test_router_builds_opencode_gateways() -> None:
    from prismal.providers.gateways.router import build_gateway_router

    router = build_gateway_router(
        gw_settings(opencode_go_enabled=True, opencode_zen_enabled=True, opencode_api_key=SENTINEL),
        org_id="acme",
    )
    assert router is not None
    assert {"opencode", "opencode-go"} <= set(router.names())
    assert router.ledger is not None
    r = router.resolve("gw:opencode-go/glm-5.3", agent="coder", session_id="s")
    assert r is not None and r.cost_mode == "subscription"
