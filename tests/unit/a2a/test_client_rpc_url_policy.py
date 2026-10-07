"""Outbound RPC URL policy — credentials never follow a card to an unchecked host.

Regression for the M5 review finding: the RPC URL adopted from a remote Agent
Card (``card.url`` / v1 interface URL) used to receive the bearer / OAuth token
without any allowlist, HTTPS, private-IP or same-origin check.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from prismal.a2a.client import A2AClient, A2AConnectionManager
from prismal.a2a.types import A2AAuth, A2AMessage, A2APart
from prismal.a2a.v1.types import ListTasksRequest
from prismal.core.exceptions import A2AAgentUnavailable

pytestmark = pytest.mark.unit

BASE = "https://good.example.com"
_DONE = {"jsonrpc": "2.0", "id": 1, "result": {"status": "completed"}}


def _card(rpc_url: str) -> dict[str, Any]:
    return {
        "name": "x",
        "description": "d",
        "version": "1",
        "url": rpc_url,
        "capabilities": {},
        "skills": [],
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        "protocolVersion": "0.3.0",
    }


def _v1_card(rpc_url: str) -> dict[str, Any]:
    return {
        "name": "x",
        "description": "d",
        "version": "1",
        "supportedInterfaces": [
            {"url": rpc_url, "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
        ],
        "capabilities": {"streaming": True},
        "skills": [],
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
    }


class _Recorder:
    def __init__(self, card: dict[str, Any]) -> None:
        self.card = card
        self.seen: list[tuple[str, str | None]] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.seen.append((str(req.url), req.headers.get("authorization")))
        if req.url.path.endswith("agent-card.json"):
            return httpx.Response(200, json=self.card)
        if req.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "CC-TOKEN"})
        return httpx.Response(
            200,
            text=f"data: {json.dumps(_DONE)}\n\n",
            headers={"content-type": "text/event-stream"},
        )

    def rpc_calls(self) -> list[tuple[str, str | None]]:
        return [s for s in self.seen if not s[0].endswith(("agent-card.json", "/token"))]


def _msg() -> A2AMessage:
    return A2AMessage(role="user", parts=[A2APart(kind="text", text="hi")], message_id="m1")


def _bearer() -> A2AAuth:
    return A2AAuth(scheme="bearer", bearer_token=SecretStr("SECRET"))


def _client(rec: _Recorder, base: str = BASE, **kw: Any) -> A2AClient:
    return A2AClient(base, http_client=httpx.AsyncClient(transport=httpx.MockTransport(rec)), **kw)


async def _send(client: A2AClient) -> None:
    async for _ in client.send_task(_msg()):
        pass


class TestCredentialsNeverLeakCrossOrigin:
    async def test_poc_strict_allowlist_card_points_to_evil_host(self) -> None:
        rec = _Recorder(_card("https://evil.attacker.net/rpc"))
        mgr = A2AConnectionManager(allowlist=["good.example.com"], strict=True, auth=_bearer())
        client = await mgr.get_client(BASE)
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(rec))
        with pytest.raises(A2AAgentUnavailable):
            await _send(client)
        assert rec.rpc_calls() == []
        assert all(auth is None for _, auth in rec.seen)

    async def test_bearer_cross_origin_denied_even_if_allowlisted(self) -> None:
        rec = _Recorder(_card("https://other.example.com/rpc"))
        client = _client(rec, auth=_bearer(), allowlist=["*.example.com"], strict=True)
        with pytest.raises(A2AAgentUnavailable, match="origin"):
            await _send(client)
        assert rec.rpc_calls() == []

    async def test_bearer_cross_origin_denied_without_allowlist(self) -> None:
        rec = _Recorder(_card("https://evil.attacker.net/rpc"))
        client = _client(rec, auth=_bearer())
        with pytest.raises(A2AAgentUnavailable):
            await _send(client)
        assert rec.rpc_calls() == []

    async def test_oauth_client_credentials_cross_origin_denied_before_token(self) -> None:
        rec = _Recorder(_card("https://evil.attacker.net/rpc"))
        auth = A2AAuth(
            scheme="oauth2_client_credentials",
            token_url=f"{BASE}/token",
            client_id="c",
            client_secret=SecretStr("s"),
        )
        client = _client(rec, auth=auth)
        with pytest.raises(A2AAgentUnavailable):
            await _send(client)
        assert rec.rpc_calls() == []

    async def test_bearer_http_rpc_url_denied(self) -> None:
        rec = _Recorder(_card("http://good.example.com/rpc"))
        client = _client(rec, auth=_bearer())
        with pytest.raises(A2AAgentUnavailable, match="HTTPS"):
            await _send(client)
        assert rec.rpc_calls() == []

    async def test_bearer_private_ip_rpc_url_denied(self) -> None:
        rec = _Recorder(_card("https://10.0.0.5/rpc"))
        client = _client(rec, base="https://10.0.0.5", auth=_bearer())
        with pytest.raises(A2AAgentUnavailable):
            await _send(client)
        assert rec.rpc_calls() == []

    async def test_private_host_opt_in_allows(self) -> None:
        rec = _Recorder(_card("https://10.0.0.5/rpc"))
        client = _client(rec, base="https://10.0.0.5", auth=_bearer(), allow_private_hosts=True)
        await _send(client)
        assert rec.rpc_calls() == [("https://10.0.0.5/rpc", "Bearer SECRET")]

    async def test_insecure_localhost_opt_in_allows(self) -> None:
        rec = _Recorder(_card("http://localhost:9000/rpc"))
        client = _client(
            rec, base="http://localhost:9000", auth=_bearer(), allow_insecure_localhost=True
        )
        await _send(client)
        assert rec.rpc_calls() == [("http://localhost:9000/rpc", "Bearer SECRET")]

    async def test_bearer_same_origin_allowed(self) -> None:
        rec = _Recorder(_card(f"{BASE}/rpc"))
        client = _client(rec, auth=_bearer(), allowlist=["good.example.com"], strict=True)
        await _send(client)
        assert rec.rpc_calls() == [(f"{BASE}/rpc", "Bearer SECRET")]

    async def test_v1_interface_url_cross_origin_denied(self) -> None:
        rec = _Recorder(_v1_card("https://evil.attacker.net/rpc"))
        client = _client(rec, auth=_bearer())
        with pytest.raises(A2AAgentUnavailable):
            await _send(client)
        with pytest.raises(A2AAgentUnavailable):
            await client.list_tasks(ListTasksRequest())
        with pytest.raises(A2AAgentUnavailable):
            await client.cancel("t1")
        assert rec.rpc_calls() == []

    async def test_cancel_v03_cross_origin_denied(self) -> None:
        rec = _Recorder(_card("https://evil.attacker.net/rpc"))
        client = _client(rec, auth=_bearer())
        with pytest.raises(A2AAgentUnavailable):
            await client.cancel("t1")
        assert rec.rpc_calls() == []


class TestNoAuthPolicy:
    async def test_default_no_auth_same_origin_unchanged(self) -> None:
        rec = _Recorder(_card(f"{BASE}/rpc"))
        await _send(_client(rec))
        assert rec.rpc_calls() == [(f"{BASE}/rpc", None)]

    async def test_default_no_auth_no_allowlist_cross_origin_allowed(self) -> None:
        rec = _Recorder(_card("https://rpc.other.net/a2a"))
        await _send(_client(rec))
        assert rec.rpc_calls() == [("https://rpc.other.net/a2a", None)]

    async def test_no_auth_cross_origin_must_pass_allowlist(self) -> None:
        rec = _Recorder(_card("https://evil.attacker.net/rpc"))
        client = _client(rec, allowlist=["good.example.com"])
        with pytest.raises(A2AAgentUnavailable, match="allowlist"):
            await _send(client)
        assert rec.rpc_calls() == []

    async def test_no_auth_cross_origin_allowlisted_allowed(self) -> None:
        rec = _Recorder(_card("https://rpc.example.com/a2a"))
        client = _client(rec, allowlist=["*.example.com"], strict=True)
        await _send(client)
        assert rec.rpc_calls() == [("https://rpc.example.com/a2a", None)]

    async def test_strict_empty_allowlist_denies_rpc(self) -> None:
        rec = _Recorder(_card(f"{BASE}/rpc"))
        client = _client(rec, strict=True)
        with pytest.raises(A2AAgentUnavailable):
            await _send(client)
        assert rec.rpc_calls() == []
