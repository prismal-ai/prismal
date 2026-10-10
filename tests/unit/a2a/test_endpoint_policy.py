"""Grouped A2A OAuth / client configuration (release 3.18.0 — CI lizard gate: ≤ 15 params).

``EndpointPolicy`` carries the four host-policy opt-ins shared by the OAuth
endpoints and the A2A RPC URL check; ``OAuthBinding`` carries the local runtime
binding of an OAuth flow (vault partition, transport, clock, audit).
"""

from __future__ import annotations

import dataclasses
import inspect

import httpx
import pytest

from prismal.a2a.client import A2AClient, A2AConnectionManager
from prismal.a2a.oauth import (
    DeviceCodeFlow,
    EndpointPolicy,
    OAuthBinding,
    PkceAuthorizationCodeFlow,
)
from prismal.core.exceptions import A2AOAuthError

pytestmark = pytest.mark.unit

_LIZARD_MAX_PARAMS = 15


def _pkce(**kw: object) -> PkceAuthorizationCodeFlow:
    params: dict[str, object] = {
        "token_url": "https://idp.test/token",
        "authorize_url": "https://idp.test/authorize",
        "client_id": "cid",
        "redirect_uri": "http://localhost:8765/callback",
        "scopes": ["openid"],
        "vault_key": "a2a.billing",
    }
    params.update(kw)
    return PkceAuthorizationCodeFlow(**params)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "ctor",
    [PkceAuthorizationCodeFlow.__init__, DeviceCodeFlow.__init__, A2AClient.__init__],
)
def test_constructors_stay_under_the_lizard_parameter_limit(ctor: object) -> None:
    # +1: lizard counts ``self``; inspect.signature on the function keeps it too.
    assert len(inspect.signature(ctor).parameters) <= _LIZARD_MAX_PARAMS  # type: ignore[arg-type]


def test_endpoint_policy_defaults_are_the_strictest() -> None:
    policy = EndpointPolicy()
    assert policy == EndpointPolicy(
        allow_insecure_localhost=False, allow_private_hosts=False, allowlist=None, strict=False
    )


def test_endpoint_policy_is_frozen_and_normalises_the_allowlist() -> None:
    policy = EndpointPolicy(allowlist=["*.acme"])
    assert policy.allowlist == ("*.acme",)
    assert EndpointPolicy(allowlist=[]).allowlist is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.strict = True  # type: ignore[misc]


def test_flow_rejects_insecure_localhost_by_default() -> None:
    with pytest.raises(A2AOAuthError):
        _pkce(token_url="http://localhost:9000/token")


def test_flow_policy_opts_into_insecure_localhost() -> None:
    _pkce(
        token_url="http://localhost:9000/token",
        policy=EndpointPolicy(allow_insecure_localhost=True),
    )


def test_flow_binding_sets_the_local_vault_partition() -> None:
    flow = _pkce(binding=OAuthBinding(org_id="acme", principal="alice"))
    assert flow._vault_ref == "a2a.billing@acme/alice"


def test_client_keeps_the_endpoint_policy() -> None:
    policy = EndpointPolicy(allowlist=["*.acme"], strict=True)
    client = A2AClient("https://agent.acme/.well-known/agent.json", policy=policy)
    assert client._policy is policy


async def test_manager_threads_its_policy_to_clients() -> None:
    mgr = A2AConnectionManager(allowlist=["*.acme"], strict=True)
    client = await mgr.get_client("https://agent.acme/.well-known/agent.json")
    assert client._policy == EndpointPolicy(allowlist=["*.acme"], strict=True)
    await mgr.aclose()


def test_client_forwards_the_full_policy_to_card_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    from prismal.a2a import oauth as oauth_mod

    seen: dict[str, object] = {}

    def _select(card: object, **kw: object) -> None:
        seen.update(kw)

    monkeypatch.setattr(oauth_mod, "select_oauth_flow", _select)
    policy = EndpointPolicy(allow_insecure_localhost=True, allow_private_hosts=True)
    client = A2AClient(
        "http://localhost:9000/.well-known/agent.json",
        policy=policy,
        http_client=httpx.AsyncClient(),
    )
    client._card_v1 = type("Card", (), {"security_requirements": [{"oauth": []}]})()  # type: ignore[assignment]
    client._card_oauth_flow()
    assert seen["policy"] is policy
