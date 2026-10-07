"""v0.3 ⇄ v1 cross-talk through in-process dual-stack handlers (PRT6-05, SPEC-PRT-A2A-002..006).

No network: every HTTP exchange goes through ``httpx.MockTransport`` into an
in-process :class:`A2AServerHandler` + :func:`build_agent_card`.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from langchain_core.messages import AIMessage

from prismal.a2a.card import a2a_route_paths, build_agent_card, clear_agent_card_cache
from prismal.a2a.client import A2AClient
from prismal.a2a.server import A2AServerHandler
from prismal.a2a.types import A2AMessage, A2APart
from prismal.a2a.v1.types import ListTasksRequest
from prismal.core.config import Settings
from prismal.core.exceptions import A2AAgentUnavailable, A2AVersionError

pytestmark = pytest.mark.integration

BASE = "https://prismal.example/a2a"
REGISTRY = {"research": ["general"]}


class _EchoGraph:
    async def ainvoke(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        text = state["messages"][-1].content
        return {"messages": [*state["messages"], AIMessage(content=f"echo: {text}")]}


class _NullAudit:
    def log_event(self, event_type: str, payload: dict[str, Any]) -> None:
        return None


class _Host:
    """Minimal host: routes card + RPC paths per tenant using a2a_route_paths."""

    def __init__(self, settings: Settings, tenants: list[str]) -> None:
        self.settings = settings
        self.handler = A2AServerHandler(_EchoGraph(), settings=settings, audit=_NullAudit())
        self.routes: dict[str, tuple[str, str]] = {}
        for org in tenants:
            card_path, rpc_path = a2a_route_paths(settings, org_id=org, protocol_version=None)
            self.routes[BASE + card_path] = ("card", org)
            self.routes[BASE + rpc_path] = ("rpc", org)

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        kind, org = self.routes.get(str(request.url).split("?")[0], ("", ""))
        if kind == "card":
            card = build_agent_card(self.settings, REGISTRY, org_id=org)
            return httpx.Response(200, json=card.model_dump(by_alias=True, exclude_none=True))
        if kind != "rpc":
            return httpx.Response(404)
        payload = json.loads(request.content)
        headers = dict(request.headers)
        if "text/event-stream" in request.headers.get("accept", ""):
            lines = [
                line async for line in self.handler.stream_rpc(payload, headers=headers, org_id=org)
            ]
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content="".join(lines)
            )
        return httpx.Response(
            200, json=await self.handler.handle_rpc(payload, headers=headers, org_id=org)
        )


def _client(host: _Host, org: str, **kw: Any) -> A2AClient:
    card_path, _ = a2a_route_paths(host.settings, org_id=org, protocol_version=None)
    return A2AClient(
        BASE + card_path,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(host)),
        **kw,
    )


def _msg(text: str) -> A2AMessage:
    return A2AMessage(role="user", parts=[A2APart(kind="text", text=text)], message_id="m")


@pytest.fixture(autouse=True)
def _fresh_cards() -> None:
    clear_agent_card_cache()


def _settings(**kw: Any) -> Settings:
    return Settings(a2a_enabled=True, a2a_base_url=BASE, a2a_strict=False, **kw)


async def test_v1_client_to_v1_host_send_and_list() -> None:
    host = _Host(_settings(a2a_protocol_version="1.0"), ["acme", "beta"])
    client = _client(host, "acme")
    arts = [a async for a in client.send_task(_msg("hello"))]
    assert client.protocol_version == "1.0"
    assert arts[0].parts[0].text == "echo: hello"
    beta = _client(host, "beta")
    _ = [a async for a in beta.send_task(_msg("other"))]
    page = await client.list_tasks(ListTasksRequest(page_size=10))
    assert page.total_size == 1
    assert page.next_page_token == ""


async def test_v03_client_to_v03_default_host_unchanged() -> None:
    host = _Host(_settings(), ["acme"])
    client = _client(host, "acme")
    arts = [a async for a in client.send_task(_msg("legacy"))]
    assert client.protocol_version == "0.3"
    assert arts[0].parts[0].text == "echo: legacy"
    with pytest.raises(A2AVersionError):
        await client.list_tasks(ListTasksRequest())


async def test_v1_client_and_legacy_caller_share_dual_stack_host() -> None:
    host = _Host(_settings(a2a_protocol_version="1.0", a2a_accept_legacy=True), ["acme"])
    client = _client(host, "acme", protocol="1.0")
    _ = [a async for a in client.send_task(_msg("v1"))]
    # a legacy caller hitting the same dual-stack endpoint is still served
    legacy = await host.handler.handle_rpc(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "message/send",
            "params": {"message": _msg("old").model_dump(by_alias=True)},
        },
        headers={},
        org_id="acme",
    )
    assert legacy["result"]["status"] == "completed"


async def test_v1_only_host_refuses_legacy_client() -> None:
    v1_only = _settings(a2a_protocol_version="1.0", a2a_accept_legacy=False)
    host = _Host(v1_only, ["acme"])
    # the client talks v0.3 to a card it was handed out of band
    v03_settings = _settings()
    card = build_agent_card(v03_settings, REGISTRY, org_id="acme", protocol_version="0.3")
    client = A2AClient(card, http_client=httpx.AsyncClient(transport=httpx.MockTransport(host)))
    with pytest.raises(A2AAgentUnavailable, match="-32009"):
        _ = [a async for a in client.send_task(_msg("old"))]
