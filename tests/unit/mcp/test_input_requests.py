"""Tests for multi-round-trip input requests (SPEC-PRT-MTK-002, PRT4-04/06)."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from prismal.mcp.input_requests import (
    DeclineAllInputHandler,
    InputRequestHandler,
    McpInputRequest,
    McpInputResponse,
    answer_input_requests,
    parse_input_request,
)

pytestmark = pytest.mark.unit


class _Metrics:
    def __init__(self) -> None:
        self.counters: list[tuple[str, int, dict[str, Any]]] = []

    def increment_counter(
        self, metric: str, value: int = 1, attributes: dict[str, Any] | None = None
    ) -> None:
        self.counters.append((metric, value, dict(attributes or {})))


class _Audit:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def log_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append((event_type, payload))


def _elicit(message: str = "Your password?", **params: Any) -> dict[str, Any]:
    return {
        "method": "elicitation/create",
        "params": {"mode": "form", "message": message, "requestedSchema": {}, **params},
    }


class TestParse:
    def test_form_elicitation_is_sanitized(self) -> None:
        req = parse_input_request("k1", _elicit("hi\x00\x07 there​"))
        assert req.kind == "elicitation"
        assert req.mode == "form"
        assert "\x00" not in req.message
        assert "\x07" not in req.message
        assert req.key == "k1"

    def test_url_elicitation_keeps_sanitized_url(self) -> None:
        req = parse_input_request(
            "k",
            {
                "method": "elicitation/create",
                "params": {"mode": "url", "message": "go", "url": "https://x/\x00a"},
            },
        )
        assert req.mode == "url"
        assert req.url == "https://x/a"

    def test_sampling_and_roots(self) -> None:
        assert parse_input_request(
            "a", {"method": "sampling/createMessage", "params": {}}
        ).kind == ("sampling")
        assert parse_input_request("b", {"method": "roots/list"}).kind == "roots"

    def test_unknown_method(self) -> None:
        assert parse_input_request("c", {"method": "weird"}).kind == "unknown"
        assert parse_input_request("d", "garbage").kind == "unknown"


class TestResponseWire:
    def test_decline_wire(self) -> None:
        assert McpInputResponse(action="decline").to_wire() == {"action": "decline"}

    def test_accept_wire_with_content(self) -> None:
        wire = McpInputResponse(action="accept", content={"a": 1}).to_wire()
        assert wire == {"action": "accept", "content": {"a": 1}}


class TestDeclineAll:
    async def test_declines_and_audits_hash_only(self) -> None:
        audit = _Audit()
        handler = DeclineAllInputHandler(audit=audit)
        assert isinstance(handler, InputRequestHandler)
        req = parse_input_request("k", _elicit("secret prompt"))
        resp = await handler.handle(req, server="srv", org_id="o1")
        assert resp.action == "decline"
        assert len(audit.events) == 1
        event, payload = audit.events[0]
        assert event == "mcp.input_request"
        assert payload["prompt_sha256"] == hashlib.sha256(b"secret prompt").hexdigest()
        assert "secret prompt" not in repr(payload)
        assert payload["server"] == "srv"
        assert payload["org_id"] == "o1"
        assert payload["action"] == "decline"

    async def test_never_opens_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import webbrowser

        opened: list[str] = []
        monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url))
        req = parse_input_request(
            "k",
            {
                "method": "elicitation/create",
                "params": {"mode": "url", "message": "m", "url": "https://evil"},
            },
        )
        resp = await DeclineAllInputHandler(audit=_Audit()).handle(req, server="s", org_id=None)
        assert resp.action == "decline"
        assert opened == []

    async def test_audit_failure_never_raises(self) -> None:
        class _Boom:
            def log_event(self, event_type: str, payload: dict[str, Any]) -> None:
                raise RuntimeError("down")

        req = parse_input_request("k", _elicit())
        resp = await DeclineAllInputHandler(audit=_Boom()).handle(req, server="s", org_id=None)
        assert resp.action == "decline"


class TestAnswer:
    async def test_default_handler_declines_elicitation_and_counts(self) -> None:
        m = _Metrics()
        out = await answer_input_requests(
            {"e": _elicit()}, handler=None, server="s", org_id=None, stateless=True, metrics=m
        )
        assert out == {"e": {"action": "decline"}}
        assert ("mcp_input_requests", 1, {"kind": "elicitation", "action": "decline"}) in m.counters

    async def test_stateless_declines_sampling_and_roots_without_handler_call(self) -> None:
        calls: list[McpInputRequest] = []

        class _Accept:
            async def handle(
                self, request: McpInputRequest, *, server: str, org_id: str | None
            ) -> McpInputResponse:
                calls.append(request)
                return McpInputResponse(action="accept", content={"ok": True})

        m = _Metrics()
        out = await answer_input_requests(
            {
                "s": {"method": "sampling/createMessage", "params": {}},
                "r": {"method": "roots/list"},
                "e": _elicit(),
            },
            handler=_Accept(),
            server="srv",
            org_id=None,
            stateless=True,
            metrics=m,
        )
        assert out["s"] == {"action": "decline"}
        assert out["r"] == {"action": "decline"}
        assert out["e"] == {"action": "accept", "content": {"ok": True}}
        assert [r.key for r in calls] == ["e"]
        assert ("mcp_input_requests", 1, {"kind": "sampling", "action": "decline"}) in m.counters

    async def test_handler_exception_declines(self) -> None:
        class _Broken:
            async def handle(
                self, request: McpInputRequest, *, server: str, org_id: str | None
            ) -> McpInputResponse:
                raise RuntimeError("x")

        out = await answer_input_requests(
            {"e": _elicit()}, handler=_Broken(), server="s", org_id=None, metrics=_Metrics()
        )
        assert out == {"e": {"action": "decline"}}

    async def test_default_metrics_path(self) -> None:
        out = await answer_input_requests(
            {"u": {"method": "nope"}}, handler=None, server="s", org_id=None
        )
        assert out == {"u": {"action": "decline"}}
