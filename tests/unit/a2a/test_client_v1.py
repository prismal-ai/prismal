"""Outbound A2A client against v1 peers (SPEC-PRT-A2A-006, OAU-001, PRT2-08/09)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from langchain_core.messages import AIMessage, HumanMessage

from prismal.a2a import client as client_mod
from prismal.a2a.client import A2AAgentNode, A2AClient
from prismal.a2a.provider import A2AToolProvider
from prismal.a2a.types import A2AAuth, A2AMessage, A2APart, AgentCard
from prismal.a2a.v1.types import ListTasksRequest, ListTasksResult
from prismal.core.exceptions import A2AOAuthError, A2AVersionError

pytestmark = pytest.mark.unit

CARD_URL = "https://billing.acme/.well-known/agent-card.json"
V1_RPC = "https://billing.acme/a2a/v1"
LEGACY_RPC = "https://billing.acme/a2a/legacy"


def _v1_card(**extra: Any) -> dict[str, Any]:
    card = {
        "name": "billing",
        "description": "Billing agent",
        "supportedInterfaces": [
            {
                "url": V1_RPC,
                "protocolBinding": "JSONRPC",
                "protocolVersion": "1.0",
                "tenant": "acme",
            },
            {"url": LEGACY_RPC, "protocolBinding": "JSONRPC", "protocolVersion": "0.3"},
        ],
        "version": "2.0.0",
        "capabilities": {"streaming": True},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [
            {"id": "create_invoice", "name": "Create Invoice", "description": "d", "tags": ["b"]}
        ],
    }
    card.update(extra)
    return card


V03_CARD = {
    "name": "billing",
    "description": "d",
    "url": LEGACY_RPC,
    "version": "1",
    "protocolVersion": "0.3.0",
    "skills": [],
}


def _sse(*events: dict[str, Any]) -> bytes:
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode()


def _ev(result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": "1", "result": result}


ARTIFACT_EV = _ev(
    {
        "artifactUpdate": {
            "taskId": "t",
            "contextId": "c",
            "artifact": {"artifactId": "a1", "parts": [{"text": "invoice #42"}]},
        }
    }
)
DONE_EV = _ev(
    {
        "statusUpdate": {
            "taskId": "t",
            "contextId": "c",
            "status": {"state": "TASK_STATE_COMPLETED"},
        }
    }
)


def _stream(*events: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_sse(*events))


def _msg(text: str = "bill") -> A2AMessage:
    return A2AMessage(role="user", parts=[A2APart(kind="text", text=text)], message_id="m1")


@pytest.fixture(autouse=True)
def counts(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    seen: list[dict[str, str]] = []
    monkeypatch.setattr(client_mod, "count_a2a_request", lambda **kw: seen.append(kw))
    return seen


class TestDiscovery:
    @respx.mock
    async def test_auto_selects_v1_and_projects_card(self) -> None:
        respx.get(CARD_URL).mock(return_value=httpx.Response(200, json=_v1_card()))
        client = A2AClient(CARD_URL)
        card = await client.discover()
        assert isinstance(card, AgentCard)
        assert card.url == V1_RPC
        assert client.protocol_version == "1.0"
        assert card.skills[0].id == "create_invoice"
        await client.aclose()

    @respx.mock
    async def test_auto_v03_card_unchanged(self) -> None:
        respx.get(CARD_URL).mock(return_value=httpx.Response(200, json=V03_CARD))
        client = A2AClient(CARD_URL)
        card = await client.discover()
        assert card.url == LEGACY_RPC
        assert client.protocol_version == "0.3"
        await client.aclose()

    @respx.mock
    async def test_forced_legacy_uses_legacy_interface_of_v1_card(self) -> None:
        respx.get(CARD_URL).mock(return_value=httpx.Response(200, json=_v1_card()))
        client = A2AClient(CARD_URL, protocol="0.3")
        card = await client.discover()
        assert card.url == LEGACY_RPC
        assert client.protocol_version == "0.3"
        await client.aclose()

    @respx.mock
    async def test_forced_version_not_advertised_raises(self) -> None:
        respx.get(CARD_URL).mock(return_value=httpx.Response(200, json=V03_CARD))
        client = A2AClient(CARD_URL, protocol="1.0")
        with pytest.raises(A2AVersionError):
            await client.discover()
        await client.aclose()

    def test_protocol_unknown_until_discovered(self) -> None:
        assert A2AClient(CARD_URL).protocol_version is None
        assert A2AClient(CARD_URL, protocol="1.0").protocol_version is None

    def test_bad_protocol_rejected(self) -> None:
        with pytest.raises(ValueError):
            A2AClient(CARD_URL, protocol="2.0")  # type: ignore[arg-type]


class TestSendV1:
    @respx.mock
    async def test_send_task_v1_envelope_and_artifacts(self, counts: list[Any]) -> None:
        respx.get(CARD_URL).mock(return_value=httpx.Response(200, json=_v1_card()))
        route = respx.post(V1_RPC).mock(return_value=_stream(ARTIFACT_EV, DONE_EV))
        client = A2AClient(CARD_URL)
        arts = [a async for a in client.send_task(_msg(), skill_id="create_invoice")]
        assert [a.parts[0].text for a in arts] == ["invoice #42"]
        req = route.calls.last.request
        assert req.headers["A2A-Version"] == "1.0"
        body = json.loads(req.content)
        assert body["method"] == "SendStreamingMessage"
        assert body["params"]["tenant"] == "acme"  # from the card interface
        assert body["params"]["message"]["role"] == "ROLE_USER"
        assert body["params"]["message"]["parts"] == [{"text": "bill"}]
        assert body["params"]["metadata"] == {"skillId": "create_invoice"}
        assert counts[-1] == {
            "protocol_version": "1.0",
            "direction": "outbound",
            "method": "SendStreamingMessage",
            "outcome": "ok",
        }
        await client.aclose()

    @respx.mock
    async def test_explicit_tenant_wins(self) -> None:
        respx.get(CARD_URL).mock(return_value=httpx.Response(200, json=_v1_card()))
        route = respx.post(V1_RPC).mock(return_value=_stream(DONE_EV))
        client = A2AClient(CARD_URL, tenant="other")
        _ = [a async for a in client.send_task(_msg())]
        assert json.loads(route.calls.last.request.content)["params"]["tenant"] == "other"
        await client.aclose()

    @respx.mock
    async def test_task_and_message_results(self) -> None:
        respx.get(CARD_URL).mock(return_value=httpx.Response(200, json=_v1_card()))
        task_ev = _ev(
            {
                "task": {
                    "id": "t",
                    "contextId": "c",
                    "status": {"state": "TASK_STATE_COMPLETED"},
                    "artifacts": [{"artifactId": "a", "parts": [{"text": "from task"}]}],
                }
            }
        )
        respx.post(V1_RPC).mock(return_value=_stream(task_ev, ARTIFACT_EV))
        client = A2AClient(CARD_URL)
        arts = [a async for a in client.send_task(_msg())]
        assert [a.parts[0].text for a in arts] == ["from task"]  # terminal task ends the stream

        msg_ev = _ev(
            {"message": {"messageId": "m", "role": "ROLE_AGENT", "parts": [{"text": "hi"}]}}
        )
        respx.post(V1_RPC).mock(return_value=_stream(msg_ev))
        arts = [a async for a in client.send_task(_msg())]
        assert [a.parts[0].text for a in arts] == ["hi"]
        await client.aclose()

    @respx.mock
    async def test_v1_error_event_raises(self, counts: list[Any]) -> None:
        from prismal.core.exceptions import A2AAgentUnavailable

        respx.get(CARD_URL).mock(return_value=httpx.Response(200, json=_v1_card()))
        err = {"jsonrpc": "2.0", "id": "1", "error": {"code": -32001, "message": "nf"}}
        respx.post(V1_RPC).mock(return_value=_stream(err))
        client = A2AClient(CARD_URL)
        with pytest.raises(A2AAgentUnavailable):
            _ = [a async for a in client.send_task(_msg())]
        assert counts[-1]["outcome"] == "error"
        await client.aclose()

    @respx.mock
    async def test_garbage_events_are_skipped(self) -> None:
        respx.get(CARD_URL).mock(return_value=httpx.Response(200, json=_v1_card()))
        content = b"data: not-json\n\n" + _sse(_ev({"statusUpdate": "x"}), _ev([1]), DONE_EV)
        respx.post(V1_RPC).mock(
            return_value=httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=content
            )
        )
        client = A2AClient(CARD_URL)
        assert [a async for a in client.send_task(_msg())] == []
        await client.aclose()

    @respx.mock
    async def test_http_failure_maps_to_unavailable(self) -> None:
        from prismal.core.exceptions import A2AAgentUnavailable

        respx.get(CARD_URL).mock(return_value=httpx.Response(200, json=_v1_card()))
        respx.post(V1_RPC).mock(side_effect=httpx.ConnectError("down"))
        client = A2AClient(CARD_URL)
        with pytest.raises(A2AAgentUnavailable):
            _ = [a async for a in client.send_task(_msg())]
        await client.aclose()

    @respx.mock
    async def test_cancel_v1(self) -> None:
        respx.get(CARD_URL).mock(return_value=httpx.Response(200, json=_v1_card()))
        route = respx.post(V1_RPC).mock(return_value=httpx.Response(200, json={"result": {}}))
        client = A2AClient(CARD_URL)
        await client.cancel("t1")
        body = json.loads(route.calls.last.request.content)
        assert body["method"] == "CancelTask"
        assert body["params"] == {"id": "t1", "tenant": "acme"}
        await client.aclose()


class TestListTasks:
    @respx.mock
    async def test_list_tasks_v03_peer_raises_before_io(self) -> None:
        card = AgentCard.model_validate(V03_CARD)
        client = A2AClient(card)
        with pytest.raises(A2AVersionError):
            await client.list_tasks(ListTasksRequest())
        assert respx.calls.call_count == 0

    @respx.mock
    async def test_list_tasks_forced_legacy_raises_before_io(self) -> None:
        client = A2AClient(CARD_URL, protocol="0.3")
        with pytest.raises(A2AVersionError):
            await client.list_tasks(ListTasksRequest())
        assert respx.calls.call_count == 0

    @respx.mock
    async def test_list_tasks_v1(self) -> None:
        respx.get(CARD_URL).mock(return_value=httpx.Response(200, json=_v1_card()))
        result = {"tasks": [], "nextPageToken": "", "pageSize": 50, "totalSize": 0}
        route = respx.post(V1_RPC).mock(
            return_value=httpx.Response(200, json={"jsonrpc": "2.0", "id": "1", "result": result})
        )
        client = A2AClient(CARD_URL)
        page = await client.list_tasks(ListTasksRequest(page_size=50))
        assert isinstance(page, ListTasksResult)
        body = json.loads(route.calls.last.request.content)
        assert body["method"] == "ListTasks"
        assert body["params"] == {"pageSize": 50, "tenant": "acme"}
        assert route.calls.last.request.headers["A2A-Version"] == "1.0"
        await client.aclose()

    @respx.mock
    async def test_list_tasks_error_raises(self) -> None:
        from prismal.core.exceptions import A2AAgentUnavailable

        respx.get(CARD_URL).mock(return_value=httpx.Response(200, json=_v1_card()))
        respx.post(V1_RPC).mock(
            return_value=httpx.Response(
                200, json={"jsonrpc": "2.0", "id": "1", "error": {"code": -32602, "message": "x"}}
            )
        )
        client = A2AClient(CARD_URL)
        with pytest.raises(A2AAgentUnavailable):
            await client.list_tasks(ListTasksRequest())
        await client.aclose()


DEVICE_SCHEMES = {
    "securitySchemes": {
        "device": {
            "oauth2SecurityScheme": {
                "flows": {
                    "deviceCode": {
                        "deviceAuthorizationUrl": "https://idp.acme/device",
                        "tokenUrl": "https://idp.acme/token",
                        "scopes": {"r": "read"},
                    }
                }
            }
        }
    },
    "securityRequirements": [{"schemes": {"device": {"list": ["r"]}}}],
}


class _Prompter:
    def __init__(self) -> None:
        self.devices: list[str] = []

    async def authorize_url(self, url: str, *, state: str) -> str:  # pragma: no cover
        raise AssertionError("not used")

    async def device_code(self, verification_uri: str, user_code: str, expires_in_s: int) -> None:
        self.devices.append(user_code)


class TestCardDrivenOAuth:
    @respx.mock
    async def test_device_flow_selected_from_card(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import prismal.a2a.oauth as oauth_mod

        async def _no_sleep(_: float) -> None:
            return None

        monkeypatch.setattr(oauth_mod.asyncio, "sleep", _no_sleep)
        respx.get(CARD_URL).mock(return_value=httpx.Response(200, json=_v1_card(**DEVICE_SCHEMES)))
        respx.post("https://idp.acme/device").mock(
            return_value=httpx.Response(
                200,
                json={
                    "device_code": "d",
                    "user_code": "U-1",
                    "verification_uri": "https://idp.acme/activate",
                    "expires_in": 600,
                    "interval": 1,
                },
            )
        )
        respx.post("https://idp.acme/token").mock(
            return_value=httpx.Response(200, json={"access_token": "dev-tok", "expires_in": 600})
        )
        rpc = respx.post(V1_RPC).mock(return_value=_stream(DONE_EV))
        prompter = _Prompter()
        client = A2AClient(CARD_URL, prompter=prompter, auth=A2AAuth(client_id="cid"))
        _ = [a async for a in client.send_task(_msg())]
        assert prompter.devices == ["U-1"]
        assert rpc.calls.last.request.headers["authorization"] == "Bearer dev-tok"
        await client.aclose()

    @respx.mock
    async def test_required_interactive_oauth_without_prompter_raises(self) -> None:
        respx.get(CARD_URL).mock(return_value=httpx.Response(200, json=_v1_card(**DEVICE_SCHEMES)))
        rpc = respx.post(V1_RPC).mock(return_value=_stream(DONE_EV))
        client = A2AClient(CARD_URL)
        with pytest.raises(A2AOAuthError):
            _ = [a async for a in client.send_task(_msg())]
        assert rpc.call_count == 0
        await client.aclose()

    @respx.mock
    async def test_explicit_bearer_skips_card_oauth(self) -> None:
        respx.get(CARD_URL).mock(return_value=httpx.Response(200, json=_v1_card(**DEVICE_SCHEMES)))
        rpc = respx.post(V1_RPC).mock(return_value=_stream(DONE_EV))
        client = A2AClient(CARD_URL, auth=A2AAuth(scheme="bearer", bearer_token="tok"))
        _ = [a async for a in client.send_task(_msg())]
        assert rpc.calls.last.request.headers["authorization"] == "Bearer tok"
        await client.aclose()


class TestNoApiChangeConsumers:
    """PRT2-09: provider and node work against v1 peers without API changes."""

    async def test_tool_provider_with_v1_peer(self) -> None:
        with respx.mock:
            respx.get(CARD_URL).mock(return_value=httpx.Response(200, json=_v1_card()))
            route = respx.post(V1_RPC).mock(return_value=_stream(ARTIFACT_EV, DONE_EV))
            provider = A2AToolProvider([CARD_URL])
            await provider.prepare()
            tools = provider.get_tools(agent_name="any")
            assert [t.name for t in tools] == ["a2a__billing__create_invoice"]
            out = await tools[0].ainvoke({"query": "bill"})
            assert "invoice #42" in out
            assert json.loads(route.calls.last.request.content)["method"] == "SendStreamingMessage"

    async def test_agent_node_with_v1_peer(self) -> None:
        with respx.mock:
            respx.get(CARD_URL).mock(return_value=httpx.Response(200, json=_v1_card()))
            respx.post(V1_RPC).mock(return_value=_stream(ARTIFACT_EV, DONE_EV))
            node = A2AAgentNode(CARD_URL).as_node(name="billing_v1")
            update = await node({"messages": [HumanMessage(content="bill")]})
            out = next(m for m in update["messages"] if isinstance(m, AIMessage)).content
            assert "invoice #42" in out
            assert update["metadata"]["a2a"]["error"] is False


class _SpyVault:
    """Records every vault ref touched (read or write)."""

    def __init__(self) -> None:
        from prismal.identity.vault import FakeVault

        self._inner = FakeVault()
        self.refs: list[str] = []

    def resolve(self, ref: str, *, scopes: Any = ()) -> Any:
        self.refs.append(ref)
        return self._inner.resolve(ref, scopes=scopes)

    def store(self, ref: str, value: Any, *, scopes: Any = ()) -> None:
        self.refs.append(ref)
        self._inner.store(ref, value, scopes=scopes)


def _device_card(tenant: str) -> dict[str, Any]:
    card = _v1_card(**DEVICE_SCHEMES)
    card["supportedInterfaces"][0]["tenant"] = tenant
    return card


class TestLocalVaultPartition:
    """Security regression: the OAuth vault partition never comes from the remote card."""

    async def _run(self, monkeypatch: pytest.MonkeyPatch, tenant: str, **kw: Any) -> list[str]:
        import prismal.a2a.oauth as oauth_mod

        async def _no_sleep(_: float) -> None:
            return None

        monkeypatch.setattr(oauth_mod.asyncio, "sleep", _no_sleep)
        vault = _SpyVault()
        with respx.mock:
            respx.get(CARD_URL).mock(return_value=httpx.Response(200, json=_device_card(tenant)))
            respx.post("https://idp.acme/device").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "device_code": "d",
                        "user_code": "U",
                        "verification_uri": "v",
                        "expires_in": 600,
                        "interval": 1,
                    },
                )
            )
            respx.post("https://idp.acme/token").mock(
                return_value=httpx.Response(
                    200, json={"access_token": "t", "expires_in": 600, "refresh_token": "r"}
                )
            )
            rpc = respx.post(V1_RPC).mock(return_value=_stream(DONE_EV))
            client = A2AClient(
                CARD_URL, prompter=_Prompter(), auth=A2AAuth(client_id="cid"), vault=vault, **kw
            )
            _ = [a async for a in client.send_task(_msg())]
            # the outbound A2A tenant param still comes from the card
            assert json.loads(rpc.calls.last.request.content)["params"]["tenant"] == tenant
            await client.aclose()
        return vault.refs

    async def test_card_tenant_never_selects_partition(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        refs = await self._run(monkeypatch, "victim-org")
        assert refs
        assert all(r.endswith("@_default") for r in refs)
        assert not any("victim-org" in r for r in refs)

    async def test_same_local_org_shares_one_partition(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        a = await self._run(monkeypatch, "tenant-a", local_org_id="acme")
        b = await self._run(monkeypatch, "tenant-b", local_org_id="acme")
        assert {r.split("@")[1] for r in a + b} == {"acme"}

    async def test_principal_suffix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        refs = await self._run(monkeypatch, "x", local_org_id="acme", principal="alice")
        assert all(r.endswith("@acme/alice") for r in refs)

    @respx.mock
    async def test_strict_empty_allowlist_rejects_card_token_url(self) -> None:
        respx.get(CARD_URL).mock(return_value=httpx.Response(200, json=_device_card("t")))
        rpc = respx.post(V1_RPC).mock(return_value=_stream(DONE_EV))
        client = A2AClient(CARD_URL, prompter=_Prompter(), strict=True)
        with pytest.raises(A2AOAuthError):
            _ = [a async for a in client.send_task(_msg())]
        assert rpc.call_count == 0
        await client.aclose()


class TestManagerThreading:
    async def test_manager_passes_local_context_to_clients(self) -> None:
        from prismal.a2a.client import A2AConnectionManager

        prompter = _Prompter()
        mgr = A2AConnectionManager(
            allowlist=["*.acme"],
            strict=True,
            local_org_id="acme",
            principal="alice",
            prompter=prompter,
        )
        client = await mgr.get_client(CARD_URL)
        assert client._local_org_id == "acme"
        assert client._principal == "alice"
        assert client._strict is True
        assert client._allowlist == ["*.acme"]
        assert client._prompter is prompter
        await mgr.aclose()
