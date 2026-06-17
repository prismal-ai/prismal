"""Inbound A2A server handler (Phase I — SPEC-A2A-003)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from prismal.a2a.server import A2AServerHandler, AuthContext
from prismal.a2a.types import A2AMessage, A2APart
from prismal.core.config import Settings

pytestmark = pytest.mark.unit


class _FakeGraph:
    """Echo graph capturing the (sanitized) input it receives."""

    def __init__(self) -> None:
        self.last_state: dict[str, Any] | None = None

    async def ainvoke(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        self.last_state = state
        text = state["messages"][-1].content
        return {"messages": [*state["messages"], AIMessage(content=f"echo: {text}")]}


class _SpyAudit:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def log_event(self, event_type: str, payload: dict) -> None:
        self.events.append((event_type, payload))


def _send_request(text: str = "hello", skill_id: str | None = None) -> dict:
    msg = A2AMessage(role="user", parts=[A2APart(kind="text", text=text)], message_id="m1")
    params: dict[str, Any] = {"message": msg.model_dump(by_alias=True)}
    if skill_id is not None:
        params["skillId"] = skill_id
    return {"jsonrpc": "2.0", "id": "req1", "method": "message/send", "params": params}


def _handler(
    graph: Any | None = None, *, strict: bool = False, audit: Any = None
) -> A2AServerHandler:
    settings = Settings(a2a_enabled=True, a2a_inbound_enabled=True, a2a_strict=strict)
    return A2AServerHandler(graph or _FakeGraph(), settings=settings, audit=audit)


class TestHandleRpc:
    async def test_message_send_maps_to_graph(self) -> None:
        handler = _handler()
        resp = await handler.handle_rpc(
            _send_request("bill me"), auth_ctx=AuthContext(authenticated=True)
        )
        assert resp["id"] == "req1"
        result = resp["result"]
        assert result["status"] == "completed"
        texts = [p["text"] for a in result["artifacts"] for p in a["parts"]]
        assert any("echo: bill me" in t for t in texts)

    async def test_unknown_method_returns_jsonrpc_error(self) -> None:
        handler = _handler()
        resp = await handler.handle_rpc(
            {"jsonrpc": "2.0", "id": "x", "method": "bogus", "params": {}},
            auth_ctx=AuthContext(authenticated=True),
        )
        assert resp["error"]["code"] == -32601

    async def test_strict_requires_auth(self) -> None:
        handler = _handler(strict=True)
        resp = await handler.handle_rpc(_send_request(), auth_ctx=None)
        assert "error" in resp
        assert resp["error"]["code"] == -32001

    async def test_non_strict_allows_no_auth(self) -> None:
        handler = _handler(strict=False)
        resp = await handler.handle_rpc(_send_request(), auth_ctx=None)
        assert resp["result"]["status"] == "completed"

    async def test_incoming_text_is_sanitized(self) -> None:
        graph = _FakeGraph()
        handler = _handler(graph)
        await handler.handle_rpc(
            _send_request("hi\x00\x07there"), auth_ctx=AuthContext(authenticated=True)
        )
        received = graph.last_state["messages"][-1].content
        assert "\x00" not in received and "\x07" not in received

    async def test_audit_logged(self) -> None:
        audit = _SpyAudit()
        handler = _handler(audit=audit)
        await handler.handle_rpc(_send_request(), auth_ctx=AuthContext(authenticated=True))
        assert any(evt == "a2a.inbound" for evt, _ in audit.events)


class TestTaskLifecycle:
    async def test_tasks_get_after_send(self) -> None:
        handler = _handler()
        send_resp = await handler.handle_rpc(
            _send_request(), auth_ctx=AuthContext(authenticated=True)
        )
        task_id = send_resp["result"]["id"]
        get_resp = await handler.handle_rpc(
            {"jsonrpc": "2.0", "id": "g", "method": "tasks/get", "params": {"id": task_id}},
            auth_ctx=AuthContext(authenticated=True),
        )
        assert get_resp["result"]["status"] == "completed"

    async def test_tasks_get_unknown_returns_error(self) -> None:
        handler = _handler()
        resp = await handler.handle_rpc(
            {"jsonrpc": "2.0", "id": "g", "method": "tasks/get", "params": {"id": "nope"}},
            auth_ctx=AuthContext(authenticated=True),
        )
        assert "error" in resp

    async def test_tasks_cancel(self) -> None:
        handler = _handler()
        send_resp = await handler.handle_rpc(
            _send_request(), auth_ctx=AuthContext(authenticated=True)
        )
        task_id = send_resp["result"]["id"]
        resp = await handler.handle_rpc(
            {"jsonrpc": "2.0", "id": "c", "method": "tasks/cancel", "params": {"id": task_id}},
            auth_ctx=AuthContext(authenticated=True),
        )
        assert resp["result"]["status"] == "canceled"


class TestStreamRpc:
    async def test_stream_yields_artifact_then_done(self) -> None:
        handler = _handler()
        events = [
            json.loads(chunk.removeprefix("data: ").strip())
            async for chunk in handler.stream_rpc(
                _send_request("stream me"), auth_ctx=AuthContext(authenticated=True)
            )
        ]
        kinds = [e["result"]["kind"] for e in events]
        assert "artifact" in kinds
        assert events[-1]["result"]["status"] == "completed"
