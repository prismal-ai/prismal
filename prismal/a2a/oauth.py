"""Interactive OAuth for outbound A2A (SPEC-PRT-OAU-001, DD-PRT-006).

v1 Agent Cards advertise ``OAuthFlows`` (``authorizationCode`` + ``pkceRequired``,
``clientCredentials``, ``deviceCode``). Client credentials stay with the
existing :class:`~prismal.a2a.client.A2AClient` path; the two *interactive*
grants live here and need a human, reached through an injected
:class:`AuthorizationPrompter` implemented by the host (dashboard redirect
handler, TUI printing the device code). No prompter ⇒ :class:`A2AOAuthError`
(never a silent downgrade).

Rules: PKCE ``S256`` only; ``state`` / ``code_verifier`` from :mod:`secrets` and
the returned ``state`` is verified; token endpoints must be HTTPS (``localhost``
excepted, with an explicit flag); refresh tokens are persisted **only** through an injected
``CredentialVaultPort`` under ``vault_key@<local org or "_default">[/<principal>]``
(the **local** org, never a remote card's tenant) — never in
``os.environ``, logs or audit (audit gets the flow + a token-hash prefix);
access tokens are :class:`~pydantic.SecretStr`.

Deviation (documented): :meth:`AuthorizationPrompter.authorize_url` returns the
**redirect URL** the IdP sent the user back to (``?code=…&state=…``) rather
than a bare code, so the engine itself can enforce the ``state`` check.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import secrets
import time
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from urllib.parse import parse_qs, urlencode, urlparse

from pydantic import SecretStr

from prismal.core.exceptions import A2AOAuthError
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    import httpx

    from prismal.a2a.types import A2AAuth
    from prismal.a2a.v1.types import AgentCardV1, OAuthFlows
    from prismal.agents.extension.ports import CredentialVaultPort

logger = get_logger(__name__)

_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
_EXPIRY_SKEW_S = 30.0
_MIN_DEVICE_INTERVAL_S = 5.0  # RFC 8628 §3.2 default; server values below are untrusted
_DEFAULT_PARTITION = "_default"
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


@runtime_checkable
class AuthorizationPrompter(Protocol):
    """Host-implemented bridge to the human (SPEC-PRT-OAU-001)."""

    async def authorize_url(self, url: str, *, state: str) -> str:
        """Send the user to *url*; return the redirect URL carrying ``code`` + ``state``."""
        ...

    async def device_code(self, verification_uri: str, user_code: str, expires_in_s: int) -> None:
        """Display the device-code instructions (no return value)."""
        ...


def _otel() -> Any:
    from prismal.monitoring.otel import OTelManager

    return OTelManager()


def _count(flow: str, outcome: str) -> None:
    try:
        _otel().increment_counter("a2a_oauth_tokens", attributes={"flow": flow, "outcome": outcome})
    except Exception:
        return


def _is_local_or_private(host: str) -> bool:
    """Literal loopback / private / link-local IPs and ``localhost`` (no DNS resolution)."""
    if host in _LOCAL_HOSTS:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


def _require_https(
    url: str,
    *,
    allow_insecure_localhost: bool = False,
    allow_private_hosts: bool = False,
    allowlist: Sequence[str] | None = None,
    strict: bool = False,
) -> None:
    """Endpoint policy for every OAuth URL (token / authorization / device).

    HTTPS only; ``http://localhost`` (and loopback literals) only with
    ``allow_insecure_localhost``; literal private / loopback / link-local IPs
    rejected unless ``allow_private_hosts`` (or loopback with the localhost
    flag); when an outbound ``allowlist`` is configured the host must match it
    (same fnmatch rule as the A2A connection manager). Under ``strict`` the host
    MUST match the allowlist and an empty allowlist denies everything (mirrors
    ``A2AConnectionManager``'s deny-all-in-strict). Hostnames are not resolved:
    the private-IP check covers literal IPs only.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    local_ok = allow_insecure_localhost and loopback
    if not host or not (parsed.scheme == "https" or (parsed.scheme == "http" and local_ok)):
        raise A2AOAuthError(f"OAuth endpoint must use HTTPS: {parsed.scheme}://{host}")
    if _is_local_or_private(host) and not (allow_private_hosts or local_ok):
        raise A2AOAuthError(f"OAuth endpoint host not allowed (private/loopback): {host}")
    if allowlist or strict:
        from prismal.a2a.client import _allowed

        if not _allowed(host, list(allowlist or ()), strict=True):
            raise A2AOAuthError(f"OAuth endpoint host not in outbound allowlist: {host}")


def _origin(url: str) -> str:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    port = parsed.port or (443 if scheme == "https" else 80)
    return f"{scheme}://{(parsed.hostname or '').lower()}:{port}"


def default_vault_key(token_url: str, agent_url: str) -> str:
    """Vault key bound to the token endpoint origin + the agent URL (never the card name)."""
    digest = hashlib.sha256(f"{_origin(token_url)}|{agent_url}".encode()).hexdigest()
    return f"a2a.oauth.{digest[:32]}"


def bind_refresh_token(token_url: str, refresh_token: str) -> SecretStr:
    """The vault value: the refresh token bound to the token endpoint that issued it."""
    return SecretStr(json.dumps({"token_url": token_url, "refresh_token": refresh_token}))


def read_bound_refresh(value: SecretStr) -> tuple[str, str] | None:
    """``(token_url, refresh_token)`` from a vault value; ``None`` when unbound/malformed."""
    try:
        obj = json.loads(value.get_secret_value())
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    token_url, refresh = obj.get("token_url"), obj.get("refresh_token")
    if isinstance(token_url, str) and isinstance(refresh, str) and refresh:
        return token_url, refresh
    return None


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:12]


class _OAuthFlow:
    """Shared caching, refresh-via-vault, HTTP and accounting."""

    _flow_name = "oauth"

    def __init__(
        self,
        *,
        token_url: str,
        client_id: str,
        scopes: Sequence[str],
        vault_key: str,
        prompter: AuthorizationPrompter | None,
        vault: CredentialVaultPort | None,
        org_id: str | None,
        http_client: httpx.AsyncClient | None,
        clock: Callable[[], float] | None,
        audit: Any | None,
        allow_insecure_localhost: bool = False,
        allow_private_hosts: bool = False,
        allowlist: Sequence[str] | None = None,
        strict: bool = False,
        principal: str | None = None,
    ) -> None:
        self._policy: dict[str, Any] = {
            "allow_insecure_localhost": allow_insecure_localhost,
            "allow_private_hosts": allow_private_hosts,
            "allowlist": list(allowlist) if allowlist else None,
            "strict": strict,
        }
        _require_https(token_url, **self._policy)
        self._token_url = token_url
        self._client_id = client_id
        self._scopes = list(scopes)
        # Partition = the LOCAL org (+ principal), never anything the remote side sent.
        partition = org_id or _DEFAULT_PARTITION
        if principal:
            partition = f"{partition}/{principal}"
        self._vault_ref = f"{vault_key}@{partition}"
        self._prompter = prompter
        self._vault = vault
        self._http_client = http_client
        self._clock = clock or time.monotonic
        self._audit = audit
        self._access: SecretStr | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(token_url={self._token_url!r}, vault_ref={self._vault_ref!r})"
        )

    def _http(self) -> httpx.AsyncClient:
        if self._http_client is None:
            import httpx

            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client

    async def _post(self, url: str, data: dict[str, str]) -> tuple[int, dict[str, Any]]:
        import httpx

        try:
            resp = await self._http().post(url, data=data, headers={"Accept": "application/json"})
            body = resp.json() if resp.content else {}
        except (httpx.HTTPError, ValueError) as exc:
            raise A2AOAuthError(f"OAuth endpoint unreachable: {type(exc).__name__}") from exc
        return resp.status_code, body if isinstance(body, dict) else {}

    def _stored_refresh(self) -> str | None:
        """The vault refresh token, only if it was issued by *this* token endpoint.

        An unbound (legacy/raw) value is ignored — never sent anywhere; a value
        bound to a different token endpoint raises (possible card spoofing).
        """
        if self._vault is None:
            return None
        try:
            value = self._vault.resolve(self._vault_ref).value
        except Exception:
            return None
        bound = read_bound_refresh(value)
        if bound is None:
            return None
        token_url, refresh = bound
        if token_url != self._token_url:
            _count("refresh", "error")
            raise A2AOAuthError("stored refresh token belongs to a different token endpoint")
        return refresh

    def _accept(self, body: dict[str, Any], flow: str) -> SecretStr:
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            raise A2AOAuthError("token response without access_token")
        expires_in = body.get("expires_in")
        ttl = float(expires_in) if isinstance(expires_in, int | float) else 3600.0
        self._access = SecretStr(token)
        self._expires_at = self._clock() + ttl
        refresh = body.get("refresh_token")
        if isinstance(refresh, str) and refresh and self._vault is not None:
            self._vault.store(self._vault_ref, bind_refresh_token(self._token_url, refresh))
        if self._audit is not None:
            self._audit.log_event("a2a.oauth", {"flow": flow, "token_hash": _token_hash(token)})
        _count(flow, "ok")
        return self._access

    async def _refresh(self) -> SecretStr | None:
        refresh = self._stored_refresh()
        if refresh is None:
            return None
        status, body = await self._post(
            self._token_url,
            {"grant_type": "refresh_token", "refresh_token": refresh, "client_id": self._client_id},
        )
        if status != 200:
            _count("refresh", "error")
            logger.info("a2a.oauth.refresh_failed", status=status)
            return None
        return self._accept(body, "refresh")

    def _cached(self) -> SecretStr | None:
        if self._access is not None and self._clock() < self._expires_at - _EXPIRY_SKEW_S:
            return self._access
        return None

    async def access_token(self) -> SecretStr:
        """A valid access token: cached → vault refresh → interactive grant.

        Serialized: concurrent first callers share one refresh / one prompt (a
        rotated refresh token is never clobbered by a racing refresh).
        """
        cached = self._cached()
        if cached is not None:
            return cached
        async with self._lock:
            return await self._acquire()

    async def _acquire(self) -> SecretStr:
        cached = self._cached()
        if cached is not None:
            return cached
        refreshed = await self._refresh()
        if refreshed is not None:
            return refreshed
        try:
            if self._prompter is None:
                raise A2AOAuthError(f"{self._flow_name} grant needs an AuthorizationPrompter")
            return await self._interactive(self._prompter)
        except A2AOAuthError:
            _count(self._flow_name, "error")
            raise

    async def _interactive(self, prompter: AuthorizationPrompter) -> SecretStr:
        raise NotImplementedError  # pragma: no cover


class PkceAuthorizationCodeFlow(_OAuthFlow):
    """Authorization code + PKCE (``S256`` only)."""

    _flow_name = "authorization_code"

    def __init__(
        self,
        *,
        token_url: str,
        authorize_url: str,
        client_id: str,
        redirect_uri: str,
        scopes: Sequence[str],
        vault_key: str,
        prompter: AuthorizationPrompter | None = None,
        vault: CredentialVaultPort | None = None,
        org_id: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] | None = None,
        audit: Any | None = None,
        code_challenge_method: str = "S256",
        allow_insecure_localhost: bool = False,
        allow_private_hosts: bool = False,
        allowlist: Sequence[str] | None = None,
        strict: bool = False,
        principal: str | None = None,
    ) -> None:
        if code_challenge_method != "S256":
            raise A2AOAuthError("only the S256 PKCE method is supported")
        super().__init__(
            token_url=token_url,
            client_id=client_id,
            scopes=scopes,
            vault_key=vault_key,
            prompter=prompter,
            vault=vault,
            org_id=org_id,
            http_client=http_client,
            clock=clock,
            audit=audit,
            allow_insecure_localhost=allow_insecure_localhost,
            allow_private_hosts=allow_private_hosts,
            allowlist=allowlist,
            strict=strict,
            principal=principal,
        )
        _require_https(authorize_url, **self._policy)
        self._authorize_url = authorize_url
        self._redirect_uri = redirect_uri

    async def _interactive(self, prompter: AuthorizationPrompter) -> SecretStr:
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
                "scope": " ".join(self._scopes),
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        redirect = await prompter.authorize_url(f"{self._authorize_url}?{query}", state=state)
        params = {k: v[0] for k, v in parse_qs(urlparse(redirect).query).items()}
        if not secrets.compare_digest(params.get("state", ""), state):
            raise A2AOAuthError("OAuth state mismatch")
        if "error" in params or not params.get("code"):
            raise A2AOAuthError(f"authorization failed: {params.get('error', 'no code')}")
        status, body = await self._post(
            self._token_url,
            {
                "grant_type": "authorization_code",
                "code": params["code"],
                "redirect_uri": self._redirect_uri,
                "client_id": self._client_id,
                "code_verifier": verifier,
            },
        )
        if status != 200:
            raise A2AOAuthError(f"token exchange failed: HTTP {status}")
        return self._accept(body, self._flow_name)


class DeviceCodeFlow(_OAuthFlow):
    """RFC 8628 device authorization grant (``interval`` / ``slow_down`` / expiry)."""

    _flow_name = "device_code"

    def __init__(
        self,
        *,
        device_authorization_url: str,
        token_url: str,
        client_id: str,
        scopes: Sequence[str],
        vault_key: str,
        prompter: AuthorizationPrompter | None = None,
        max_wait_s: int = 600,
        vault: CredentialVaultPort | None = None,
        org_id: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        audit: Any | None = None,
        allow_insecure_localhost: bool = False,
        allow_private_hosts: bool = False,
        allowlist: Sequence[str] | None = None,
        strict: bool = False,
        principal: str | None = None,
    ) -> None:
        super().__init__(
            token_url=token_url,
            client_id=client_id,
            scopes=scopes,
            vault_key=vault_key,
            prompter=prompter,
            vault=vault,
            org_id=org_id,
            http_client=http_client,
            clock=clock,
            audit=audit,
            allow_insecure_localhost=allow_insecure_localhost,
            allow_private_hosts=allow_private_hosts,
            allowlist=allowlist,
            strict=strict,
            principal=principal,
        )
        _require_https(device_authorization_url, **self._policy)
        self._device_url = device_authorization_url
        self._max_wait_s = max_wait_s
        self._sleep = sleep or asyncio.sleep

    async def _interactive(self, prompter: AuthorizationPrompter) -> SecretStr:
        status, grant = await self._post(
            self._device_url, {"client_id": self._client_id, "scope": " ".join(self._scopes)}
        )
        device_code = grant.get("device_code")
        if status != 200 or not isinstance(device_code, str):
            raise A2AOAuthError(f"device authorization failed: HTTP {status}")
        try:
            expires_in = int(grant.get("expires_in", self._max_wait_s))
            interval = float(grant.get("interval", _MIN_DEVICE_INTERVAL_S))
        except (TypeError, ValueError) as exc:
            raise A2AOAuthError("malformed device authorization response") from exc
        expires_in = max(0, min(expires_in, self._max_wait_s))
        if not interval >= _MIN_DEVICE_INTERVAL_S:  # also rejects NaN
            interval = _MIN_DEVICE_INTERVAL_S
        interval = min(interval, max(float(self._max_wait_s), _MIN_DEVICE_INTERVAL_S))
        uri = str(grant.get("verification_uri_complete") or grant.get("verification_uri", ""))
        await prompter.device_code(uri, str(grant.get("user_code", "")), expires_in)
        deadline = self._clock() + expires_in
        while True:
            await self._sleep(interval)
            if self._clock() >= deadline:
                raise A2AOAuthError("device code expired before authorization")
            status, body = await self._post(
                self._token_url,
                {
                    "grant_type": _DEVICE_GRANT,
                    "device_code": device_code,
                    "client_id": self._client_id,
                },
            )
            if status == 200:
                return self._accept(body, self._flow_name)
            error = body.get("error")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            raise A2AOAuthError(f"device authorization failed: {error or status}")


def _interactive_flows(card: AgentCardV1) -> list[OAuthFlows]:
    found: list[OAuthFlows] = []
    for scheme in (card.security_schemes or {}).values():
        oauth = scheme.oauth2_security_scheme
        if oauth is not None and (
            oauth.flows.authorization_code is not None or oauth.flows.device_code is not None
        ):
            found.append(oauth.flows)
    return found


def select_oauth_flow(
    card: AgentCardV1,
    *,
    auth: A2AAuth,
    prompter: AuthorizationPrompter | None,
    redirect_uri: str | None = None,
    vault: CredentialVaultPort | None = None,
    org_id: str | None = None,
    http_client: httpx.AsyncClient | None = None,
    vault_key: str | None = None,
    agent_url: str | None = None,
    allowlist: Sequence[str] | None = None,
    allow_insecure_localhost: bool = False,
    allow_private_hosts: bool = False,
    strict: bool = False,
    principal: str | None = None,
) -> PkceAuthorizationCodeFlow | DeviceCodeFlow | None:
    """Card-driven interactive flow selection (PKCE when a redirect URI exists, else device).

    Returns ``None`` when the card advertises no interactive OAuth flow (client
    credentials stay with :class:`~prismal.a2a.client.A2AClient`). Raises
    :class:`A2AOAuthError` when an interactive flow is the only option but no
    prompter (or no usable flow) is available — never a silent downgrade.

    The default vault key is :func:`default_vault_key` of the chosen token
    endpoint and ``agent_url`` (the URL the caller discovered the agent at;
    falls back to the card's first interface) — never the card ``name``.
    """
    flows = _interactive_flows(card)
    if not flows:
        return None
    if prompter is None:
        raise A2AOAuthError("remote agent requires interactive OAuth but no prompter is injected")
    base = agent_url or (card.supported_interfaces[0].url if card.supported_interfaces else "")
    client_id = auth.client_id or ""
    policy: dict[str, Any] = {
        "allowlist": allowlist,
        "allow_insecure_localhost": allow_insecure_localhost,
        "allow_private_hosts": allow_private_hosts,
        "strict": strict,
        "principal": principal,
    }
    for flow in flows:
        ac = flow.authorization_code
        if ac is not None and redirect_uri:
            return PkceAuthorizationCodeFlow(
                token_url=ac.token_url,
                authorize_url=ac.authorization_url,
                client_id=client_id,
                redirect_uri=redirect_uri,
                scopes=list(ac.scopes),
                vault_key=vault_key or default_vault_key(ac.token_url, base),
                prompter=prompter,
                vault=vault,
                org_id=org_id,
                http_client=http_client,
                **policy,
            )
    for flow in flows:
        dc = flow.device_code
        if dc is not None:
            return DeviceCodeFlow(
                device_authorization_url=dc.device_authorization_url,
                token_url=dc.token_url,
                client_id=client_id,
                scopes=list(dc.scopes),
                vault_key=vault_key or default_vault_key(dc.token_url, base),
                prompter=prompter,
                vault=vault,
                org_id=org_id,
                http_client=http_client,
                **policy,
            )
    raise A2AOAuthError("authorization-code flow needs a redirect URI (a2a_oauth_redirect_uri)")


__all__ = [
    "AuthorizationPrompter",
    "DeviceCodeFlow",
    "PkceAuthorizationCodeFlow",
    "bind_refresh_token",
    "default_vault_key",
    "read_bound_refresh",
    "select_oauth_flow",
]
