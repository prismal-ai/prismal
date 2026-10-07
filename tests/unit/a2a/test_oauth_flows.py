"""A2A interactive OAuth flows (SPEC-PRT-OAU-001, DD-PRT-006)."""

from __future__ import annotations

import base64
import hashlib
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import pytest
from pydantic import SecretStr

from prismal.a2a import oauth as oauth_mod
from prismal.a2a.oauth import (
    AuthorizationPrompter,
    DeviceCodeFlow,
    PkceAuthorizationCodeFlow,
    bind_refresh_token,
    default_vault_key,
    read_bound_refresh,
    select_oauth_flow,
)
from prismal.a2a.types import A2AAuth
from prismal.a2a.v1.types import AgentCardV1
from prismal.core.exceptions import A2AOAuthError
from prismal.identity.vault import FakeVault

pytestmark = pytest.mark.unit

TOKEN_URL = "https://idp.test/token"
AUTH_URL = "https://idp.test/authorize"
DEVICE_URL = "https://idp.test/device"
REDIRECT = "http://localhost:8765/callback"


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


class _Prompter:
    def __init__(self, *, tamper_state: bool = False, error: str | None = None) -> None:
        self.urls: list[str] = []
        self.devices: list[tuple[str, str, int]] = []
        self.tamper_state = tamper_state
        self.error = error

    async def authorize_url(self, url: str, *, state: str) -> str:
        self.urls.append(url)
        query: dict[str, str] = {"state": "evil" if self.tamper_state else state}
        if self.error:
            query["error"] = self.error
        else:
            query["code"] = "the-code"
        return f"{REDIRECT}?{urlencode(query)}"

    async def device_code(self, verification_uri: str, user_code: str, expires_in_s: int) -> None:
        self.devices.append((verification_uri, user_code, expires_in_s))


class _IdP:
    """Fake IdP behind httpx.MockTransport."""

    def __init__(self, device_script: list[tuple[int, dict[str, Any]]] | None = None) -> None:
        self.requests: list[dict[str, str]] = []
        self.device_script = list(device_script or [])
        self.refresh_ok = True

    def handler(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.requests.append({"url": str(request.url), **form})
        if str(request.url) == DEVICE_URL:
            return httpx.Response(
                200,
                json={
                    "device_code": "dev-1",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://idp.test/activate",
                    "expires_in": 30,
                    "interval": 2,
                },
            )
        grant = form.get("grant_type")
        if grant == "authorization_code":
            return httpx.Response(
                200,
                json={"access_token": "at-1", "expires_in": 60, "refresh_token": "rt-1"},
            )
        if grant == "refresh_token":
            if not self.refresh_ok:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": "at-refreshed", "expires_in": 60})
        if grant == "urn:ietf:params:oauth:grant-type:device_code":
            status, body = self.device_script.pop(0)
            return httpx.Response(status, json=body)
        return httpx.Response(400, json={"error": "unsupported_grant_type"})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


@pytest.fixture
def counts(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    class _Otel:
        def increment_counter(self, key: str, value: int = 1, attributes: Any = None) -> None:
            seen.append({"key": key, **(attributes or {})})

    monkeypatch.setattr(oauth_mod, "_otel", lambda: _Otel())
    return seen


def _pkce(idp: _IdP, **kw: Any) -> PkceAuthorizationCodeFlow:
    clock = kw.pop("clock", _Clock())
    params: dict[str, Any] = {
        "token_url": TOKEN_URL,
        "authorize_url": AUTH_URL,
        "client_id": "cid",
        "redirect_uri": REDIRECT,
        "scopes": ["invoices:write", "openid"],
        "vault_key": "a2a.billing",
        "prompter": _Prompter(),
        "http_client": idp.client(),
        "clock": clock,
    }
    params.update(kw)
    return PkceAuthorizationCodeFlow(**params)


def test_prompter_protocol_is_runtime_checkable() -> None:
    assert isinstance(_Prompter(), AuthorizationPrompter)


class TestPkce:
    async def test_full_grant_uses_s256_and_random_state(self, counts: list[Any]) -> None:
        idp = _IdP()
        prompter = _Prompter()
        vault = FakeVault()
        flow = _pkce(idp, prompter=prompter, vault=vault, org_id="acme")
        token = await flow.access_token()
        assert isinstance(token, SecretStr)
        assert token.get_secret_value() == "at-1"

        q = {k: v[0] for k, v in parse_qs(urlparse(prompter.urls[0]).query).items()}
        assert q["code_challenge_method"] == "S256"
        assert q["response_type"] == "code"
        assert q["scope"] == "invoices:write openid"
        assert len(q["state"]) >= 32
        exchange = idp.requests[-1]
        verifier = exchange["code_verifier"]
        assert 43 <= len(verifier) <= 128
        digest = hashlib.sha256(verifier.encode()).digest()
        assert q["code_challenge"] == base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        assert exchange["code"] == "the-code"
        # refresh token persisted only in the vault, under vault_key@org_id
        assert read_bound_refresh(vault.resolve("a2a.billing@acme").value) == (TOKEN_URL, "rt-1")
        assert {"key": "a2a_oauth_tokens", "flow": "authorization_code", "outcome": "ok"} in counts

    async def test_states_are_unique_per_grant(self) -> None:
        prompter = _Prompter()
        for _ in range(2):
            await _pkce(_IdP(), prompter=prompter).access_token()
        states = [parse_qs(urlparse(u).query)["state"][0] for u in prompter.urls]
        assert states[0] != states[1]

    async def test_cached_token_reused_until_expiry(self) -> None:
        idp = _IdP()
        clock = _Clock()
        flow = _pkce(idp, clock=clock)
        await flow.access_token()
        await flow.access_token()
        assert len(idp.requests) == 1
        clock.t += 120
        await flow.access_token()
        assert len(idp.requests) == 2

    async def test_refresh_from_vault_skips_prompter(self, counts: list[Any]) -> None:
        idp = _IdP()
        vault = FakeVault()
        vault.store("a2a.billing@acme", bind_refresh_token(TOKEN_URL, "rt-0"))
        flow = _pkce(idp, prompter=None, vault=vault, org_id="acme")
        assert (await flow.access_token()).get_secret_value() == "at-refreshed"
        assert idp.requests[0]["grant_type"] == "refresh_token"
        assert idp.requests[0]["refresh_token"] == "rt-0"
        assert {"key": "a2a_oauth_tokens", "flow": "refresh", "outcome": "ok"} in counts

    async def test_failed_refresh_falls_back_to_interactive(self) -> None:
        idp = _IdP()
        idp.refresh_ok = False
        vault = FakeVault()
        vault.store("a2a.billing@_default", bind_refresh_token(TOKEN_URL, "rt-old"))
        flow = _pkce(idp, vault=vault)
        assert (await flow.access_token()).get_secret_value() == "at-1"

    async def test_no_prompter_raises(self, counts: list[Any]) -> None:
        flow = _pkce(_IdP(), prompter=None)
        with pytest.raises(A2AOAuthError):
            await flow.access_token()
        assert {
            "key": "a2a_oauth_tokens",
            "flow": "authorization_code",
            "outcome": "error",
        } in counts

    async def test_state_mismatch_rejected(self) -> None:
        flow = _pkce(_IdP(), prompter=_Prompter(tamper_state=True))
        with pytest.raises(A2AOAuthError, match="state"):
            await flow.access_token()

    async def test_idp_error_in_redirect(self) -> None:
        flow = _pkce(_IdP(), prompter=_Prompter(error="access_denied"))
        with pytest.raises(A2AOAuthError):
            await flow.access_token()

    async def test_bare_code_rejected(self) -> None:
        class _Bare(_Prompter):
            async def authorize_url(self, url: str, *, state: str) -> str:
                return "just-a-code"

        with pytest.raises(A2AOAuthError):
            await _pkce(_IdP(), prompter=_Bare()).access_token()

    def test_plain_pkce_rejected(self) -> None:
        with pytest.raises(A2AOAuthError):
            _pkce(_IdP(), code_challenge_method="plain")

    def test_non_https_token_url_rejected(self) -> None:
        with pytest.raises(A2AOAuthError):
            _pkce(_IdP(), token_url="http://idp.test/token")

    async def test_token_endpoint_failure(self) -> None:
        def _fail(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={})

        flow = _pkce(_IdP(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(_fail)))
        with pytest.raises(A2AOAuthError):
            await flow.access_token()

    async def test_token_never_in_repr(self) -> None:
        flow = _pkce(_IdP())
        token = await flow.access_token()
        assert "at-1" not in repr(token)
        assert "at-1" not in repr(flow)


def _device(idp: _IdP, clock: _Clock, **kw: Any) -> DeviceCodeFlow:
    params: dict[str, Any] = {
        "device_authorization_url": DEVICE_URL,
        "token_url": TOKEN_URL,
        "client_id": "cid",
        "scopes": ["invoices:read"],
        "vault_key": "a2a.billing",
        "prompter": _Prompter(),
        "http_client": idp.client(),
        "clock": clock,
        "sleep": clock.sleep,
    }
    params.update(kw)
    return DeviceCodeFlow(**params)


class TestDeviceCode:
    async def test_polls_with_interval_and_slow_down(self, counts: list[Any]) -> None:
        idp = _IdP(
            device_script=[
                (400, {"error": "authorization_pending"}),
                (400, {"error": "slow_down"}),
                (200, {"access_token": "dev-at", "expires_in": 60, "refresh_token": "dev-rt"}),
            ]
        )
        clock = _Clock()
        prompter = _Prompter()
        vault = FakeVault()
        flow = _device(idp, clock, prompter=prompter, vault=vault, org_id="acme")
        assert (await flow.access_token()).get_secret_value() == "dev-at"
        assert prompter.devices == [("https://idp.test/activate", "ABCD-EFGH", 30)]
        assert clock.sleeps == [5, 5, 10]  # server interval 2 floored to RFC 8628's 5 s
        assert read_bound_refresh(vault.resolve("a2a.billing@acme").value) == (
            TOKEN_URL,
            "dev-rt",
        )
        assert {"key": "a2a_oauth_tokens", "flow": "device_code", "outcome": "ok"} in counts

    async def test_expiry_raises(self) -> None:
        idp = _IdP(device_script=[(400, {"error": "authorization_pending"})] * 50)
        with pytest.raises(A2AOAuthError, match="expired"):
            await _device(idp, _Clock()).access_token()

    async def test_max_wait_bounds_polling(self) -> None:
        idp = _IdP(device_script=[(400, {"error": "authorization_pending"})] * 50)
        clock = _Clock()
        with pytest.raises(A2AOAuthError):
            await _device(idp, clock, max_wait_s=5).access_token()
        assert sum(clock.sleeps) <= 6

    @pytest.mark.parametrize("error", ["access_denied", "expired_token", "weird"])
    async def test_terminal_errors(self, error: str) -> None:
        idp = _IdP(device_script=[(400, {"error": error})])
        with pytest.raises(A2AOAuthError):
            await _device(idp, _Clock()).access_token()

    async def test_no_prompter_raises(self) -> None:
        with pytest.raises(A2AOAuthError):
            await _device(_IdP(), _Clock(), prompter=None).access_token()

    async def test_device_endpoint_failure(self) -> None:
        def _fail(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        flow = _device(
            _IdP(), _Clock(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(_fail))
        )
        with pytest.raises(A2AOAuthError):
            await flow.access_token()


def _card(flows: dict[str, Any]) -> AgentCardV1:
    return AgentCardV1.model_validate(
        {
            "name": "n",
            "description": "d",
            "supportedInterfaces": [
                {"url": "https://h/a2a", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
            ],
            "version": "1",
            "capabilities": {},
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [],
            "securitySchemes": {"oauth": {"oauth2SecurityScheme": {"flows": flows}}},
            "securityRequirements": [{"schemes": {"oauth": {"list": ["a"]}}}],
        }
    )


AC = {
    "authorizationCode": {
        "authorizationUrl": AUTH_URL,
        "tokenUrl": TOKEN_URL,
        "scopes": {"a": "A"},
        "pkceRequired": True,
    }
}
DC = {"deviceCode": {"deviceAuthorizationUrl": DEVICE_URL, "tokenUrl": TOKEN_URL, "scopes": {}}}


class TestSelectFlow:
    def test_authorization_code_with_redirect(self) -> None:
        flow = select_oauth_flow(
            _card(AC), auth=A2AAuth(client_id="cid"), prompter=_Prompter(), redirect_uri=REDIRECT
        )
        assert isinstance(flow, PkceAuthorizationCodeFlow)

    def test_device_code_when_no_redirect(self) -> None:
        flow = select_oauth_flow(
            _card(DC), auth=A2AAuth(client_id="cid"), prompter=_Prompter(), redirect_uri=None
        )
        assert isinstance(flow, DeviceCodeFlow)

    def test_authorization_code_without_redirect_is_unusable(self) -> None:
        with pytest.raises(A2AOAuthError):
            select_oauth_flow(
                _card(AC), auth=A2AAuth(client_id="cid"), prompter=_Prompter(), redirect_uri=None
            )

    def test_required_interactive_without_prompter_raises(self) -> None:
        with pytest.raises(A2AOAuthError):
            select_oauth_flow(_card(DC), auth=A2AAuth(client_id="cid"), prompter=None)

    def test_client_credentials_only_returns_none(self) -> None:
        cc = {"clientCredentials": {"tokenUrl": TOKEN_URL, "scopes": {}}}
        assert select_oauth_flow(_card(cc), auth=A2AAuth(), prompter=None) is None

    def test_no_schemes_returns_none(self) -> None:
        card = _card(AC).model_copy(update={"security_schemes": None})
        assert select_oauth_flow(card, auth=A2AAuth(), prompter=_Prompter()) is None


class TestEndpointHardening:
    """Security regression: refresh tokens never leave for an untrusted token endpoint."""

    async def test_spoofed_card_same_name_other_token_host_gets_nothing(self) -> None:
        legit = _IdP()
        vault = FakeVault()
        prompter = _Prompter()
        good = select_oauth_flow(
            _card(AC),
            auth=A2AAuth(client_id="cid"),
            prompter=prompter,
            redirect_uri=REDIRECT,
            vault=vault,
            org_id="acme",
            http_client=legit.client(),
            agent_url="https://billing.acme/a2a",
        )
        assert good is not None
        await good.access_token()  # stores the bound refresh token

        evil = _IdP()
        spoof_flows = {
            "authorizationCode": {**AC["authorizationCode"], "tokenUrl": "https://evil.test/token"}
        }
        spoof = select_oauth_flow(
            _card(spoof_flows),  # same card name as the trusted agent
            auth=A2AAuth(client_id="cid"),
            prompter=prompter,
            redirect_uri=REDIRECT,
            vault=vault,
            org_id="acme",
            http_client=evil.client(),
            agent_url="https://billing.acme/a2a",
        )
        assert spoof is not None
        # default keys are bound to the token origin: no shared vault entry
        assert default_vault_key("https://evil.test/token", "https://billing.acme/a2a") != (
            default_vault_key(TOKEN_URL, "https://billing.acme/a2a")
        )
        await spoof.access_token()
        assert all(r.get("refresh_token") != "rt-1" for r in evil.requests)

    async def test_stored_token_url_mismatch_refuses_refresh(self) -> None:
        vault = FakeVault()
        vault.store("shared@acme", bind_refresh_token(TOKEN_URL, "rt-secret"))
        evil = _IdP()
        flow = _pkce(
            evil,
            token_url="https://evil.test/token",
            vault=vault,
            org_id="acme",
            vault_key="shared",
        )
        with pytest.raises(A2AOAuthError, match="token endpoint"):
            await flow.access_token()
        assert evil.requests == []

    async def test_unbound_legacy_vault_value_is_never_sent(self) -> None:
        idp = _IdP()
        vault = FakeVault()
        vault.store("a2a.billing@_default", SecretStr("raw-refresh"))
        flow = _pkce(idp, vault=vault)
        await flow.access_token()
        assert all(r.get("refresh_token") != "raw-refresh" for r in idp.requests)

    def test_default_vault_key_ignores_card_name_and_normalizes_origin(self) -> None:
        a = default_vault_key("https://IDP.test:443/token", "https://h/a2a")
        b = default_vault_key("https://idp.test/other", "https://h/a2a")
        assert a == b
        assert a.startswith("a2a.oauth.")
        assert len(a) == len("a2a.oauth.") + 32
        assert default_vault_key("https://idp.test:8443/t", "https://h/a2a") != a
        assert default_vault_key(TOKEN_URL, "https://other/a2a") != a

    @pytest.mark.parametrize(
        "field",
        [
            {"token_url": "http://idp.test/token"},
            {"authorize_url": "http://idp.test/authorize"},
        ],
    )
    def test_http_endpoints_rejected(self, field: dict[str, str]) -> None:
        with pytest.raises(A2AOAuthError):
            _pkce(_IdP(), **field)

    def test_http_device_url_rejected(self) -> None:
        with pytest.raises(A2AOAuthError):
            _device(_IdP(), _Clock(), device_authorization_url="http://idp.test/device")

    def test_localhost_only_with_explicit_flag(self) -> None:
        with pytest.raises(A2AOAuthError):
            _pkce(_IdP(), token_url="http://localhost:9000/token")
        _pkce(_IdP(), token_url="http://localhost:9000/token", allow_insecure_localhost=True)

    @pytest.mark.parametrize(
        "host",
        [
            "10.0.0.5",
            "192.168.1.1",
            "127.0.0.1",
            "169.254.169.254",
            "[::1]",
            "[fe80::1]",
            "localhost",
        ],
    )
    def test_private_loopback_link_local_literals_rejected(self, host: str) -> None:
        with pytest.raises(A2AOAuthError):
            _pkce(_IdP(), token_url=f"https://{host}/token")

    def test_private_hosts_allowed_explicitly(self) -> None:
        _pkce(_IdP(), token_url="https://10.0.0.5/token", allow_private_hosts=True)

    def test_allowlist_applies_to_token_host(self) -> None:
        with pytest.raises(A2AOAuthError):
            _pkce(_IdP(), allowlist=["*.acme"])
        _pkce(_IdP(), allowlist=["*.test"])

    def test_select_passes_allowlist(self) -> None:
        with pytest.raises(A2AOAuthError):
            select_oauth_flow(
                _card(AC),
                auth=A2AAuth(client_id="cid"),
                prompter=_Prompter(),
                redirect_uri=REDIRECT,
                allowlist=["*.trusted"],
            )

    async def test_legitimate_refresh_still_works(self) -> None:
        idp = _IdP()
        vault = FakeVault()
        vault.store("a2a.billing@acme", bind_refresh_token(TOKEN_URL, "rt-0"))
        flow = _pkce(idp, prompter=None, vault=vault, org_id="acme")
        assert (await flow.access_token()).get_secret_value() == "at-refreshed"


class TestStrictAndPartition:
    def test_strict_with_empty_allowlist_rejects_token_url(self) -> None:
        with pytest.raises(A2AOAuthError, match="allowlist"):
            _pkce(_IdP(), strict=True)
        with pytest.raises(A2AOAuthError):
            select_oauth_flow(
                _card(AC),
                auth=A2AAuth(client_id="cid"),
                prompter=_Prompter(),
                redirect_uri=REDIRECT,
                strict=True,
            )

    def test_strict_requires_match(self) -> None:
        with pytest.raises(A2AOAuthError):
            _pkce(_IdP(), strict=True, allowlist=["*.acme"])
        _pkce(_IdP(), strict=True, allowlist=["*.test"])

    def test_strict_applies_to_device_url(self) -> None:
        with pytest.raises(A2AOAuthError):
            _device(
                _IdP(),
                _Clock(),
                strict=True,
                allowlist=["idp.test"],
                device_authorization_url="https://other.test/device",
            )

    async def test_default_partition_without_org(self) -> None:
        vault = FakeVault()
        await _pkce(_IdP(), vault=vault).access_token()
        assert read_bound_refresh(vault.resolve("a2a.billing@_default").value) is not None

    async def test_principal_partition(self) -> None:
        vault = FakeVault()
        await _pkce(_IdP(), vault=vault, org_id="acme", principal="alice").access_token()
        assert read_bound_refresh(vault.resolve("a2a.billing@acme/alice").value) is not None


class TestDeviceCodeHardening:
    """Review fix: server-supplied ``interval`` / ``expires_in`` are untrusted."""

    @staticmethod
    def _idp(grant: dict[str, Any]) -> _IdP:
        idp = _IdP(device_script=[(200, {"access_token": "dev-at", "expires_in": 60})])
        original = idp.handler

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url) == DEVICE_URL:
                idp.requests.append({"url": DEVICE_URL})
                return httpx.Response(200, json={"device_code": "dev-1", **grant})
            return original(request)

        idp.handler = handler  # type: ignore[method-assign]
        return idp

    @pytest.mark.parametrize("interval", [0, -3, 0.01])
    async def test_interval_floored_at_5s(self, interval: float) -> None:
        clock = _Clock()
        idp = self._idp({"interval": interval, "expires_in": 30})
        assert (await _device(idp, clock).access_token()).get_secret_value() == "dev-at"
        assert clock.sleeps == [5]

    async def test_huge_interval_capped_by_max_wait(self) -> None:
        clock = _Clock()
        idp = self._idp({"interval": 1e9, "expires_in": 30})
        with pytest.raises(A2AOAuthError, match="expired"):
            await _device(idp, clock, max_wait_s=20).access_token()
        assert clock.sleeps == [20]

    @pytest.mark.parametrize("field", ["interval", "expires_in"])
    async def test_non_numeric_values_raise_oauth_error(self, field: str) -> None:
        idp = self._idp({field: "soon"})
        with pytest.raises(A2AOAuthError):
            await _device(idp, _Clock()).access_token()

    async def test_expires_in_capped_by_max_wait(self) -> None:
        prompter = _Prompter()
        idp = self._idp({"expires_in": 10**12, "verification_uri": "https://idp.test/a"})
        await _device(idp, _Clock(), prompter=prompter, max_wait_s=60).access_token()
        assert prompter.devices[0][2] == 60


class TestConcurrentAccessToken:
    async def test_concurrent_first_calls_hit_token_endpoint_once(self) -> None:
        import asyncio

        idp = _IdP()

        class _SlowPrompter(_Prompter):
            async def authorize_url(self, url: str, *, state: str) -> str:
                await asyncio.sleep(0)
                return await super().authorize_url(url, state=state)

        prompter = _SlowPrompter()
        flow = _pkce(idp, prompter=prompter)
        tokens = await asyncio.gather(*(flow.access_token() for _ in range(5)))
        assert {t.get_secret_value() for t in tokens} == {"at-1"}
        assert len(prompter.urls) == 1
        assert sum(1 for r in idp.requests if r.get("grant_type") == "authorization_code") == 1

    async def test_concurrent_refresh_hits_endpoint_once(self) -> None:
        import asyncio

        idp = _IdP()
        vault = FakeVault()
        vault.store("a2a.billing@acme", bind_refresh_token(TOKEN_URL, "rt-0"))

        async def _yielding(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(0)
            return idp.handler(request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(_yielding))
        flow = _pkce(idp, vault=vault, org_id="acme", http_client=client)
        await asyncio.gather(*(flow.access_token() for _ in range(4)))
        assert sum(1 for r in idp.requests if r.get("grant_type") == "refresh_token") == 1
