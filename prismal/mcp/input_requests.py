"""Multi-round-trip input requests for the stateless MCP wire (SPEC-PRT-MTK-002).

A 2026-07-28 server answers ``resultType: "input_required"`` with
``inputRequests: {key: elicitation/create | sampling/createMessage | roots/list}``; the client
retries the original request with ``inputResponses`` (same keys) + ``requestState``.

Security posture:

* Elicitation text and URLs are untrusted: L1-sanitized (:class:`InputSanitizer`) before any
  handler sees them; URL-mode elicitation is **never** opened by the engine.
* The default :class:`DeclineAllInputHandler` declines and audits only the SHA-256 of the
  prompt — never its content.
* Stateless mode declines sampling / roots (deprecated upstream) without consulting the handler;
  the legacy adapter keeps today's SDK behaviour.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from prismal.core.logging import get_logger
from prismal.mcp import constants as c
from prismal.mcp.protocol import MetricsSink, default_metrics
from prismal.security.sanitizer import InputSanitizer

logger = get_logger("prismal.mcp.input_requests")

InputRequestKind = Literal["elicitation", "sampling", "roots", "unknown"]
InputAction = Literal["accept", "decline", "cancel"]

_KIND_BY_METHOD: dict[str, InputRequestKind] = {
    c.METHOD_ELICITATION_CREATE: "elicitation",
    c.METHOD_SAMPLING_CREATE_MESSAGE: "sampling",
    c.METHOD_ROOTS_LIST: "roots",
}


class AuditSink(Protocol):
    """The ``log_event`` slice of :class:`~prismal.agents.extension.ports.AuditPort`."""

    def log_event(self, event_type: str, payload: dict[str, Any]) -> None: ...


@dataclass(frozen=True)
class McpInputRequest:
    """One sanitized input request; ``url`` is informational only (never opened)."""

    key: str
    kind: InputRequestKind
    method: str
    message: str = ""
    mode: str | None = None
    url: str | None = None
    requested_schema: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class McpInputResponse:
    """The client's answer for one key (``ElicitResult`` shape)."""

    action: InputAction
    content: Mapping[str, Any] | None = None

    def to_wire(self) -> dict[str, Any]:
        """Serialize for ``inputResponses[key]``."""
        wire: dict[str, Any] = {"action": self.action}
        if self.content is not None:
            wire["content"] = dict(self.content)
        return wire


@runtime_checkable
class InputRequestHandler(Protocol):
    """Answers elicitations (form | url) for a server/tenant."""

    async def handle(
        self, request: McpInputRequest, *, server: str, org_id: str | None
    ) -> McpInputResponse: ...


_DECLINE = McpInputResponse(action="decline")


def parse_input_request(
    key: str, payload: Any, *, sanitizer: InputSanitizer | None = None
) -> McpInputRequest:
    """Parse + L1-sanitize one ``inputRequests`` entry (unknown shapes ⇒ ``kind="unknown"``)."""
    san = sanitizer or InputSanitizer()
    if not isinstance(payload, Mapping):
        return McpInputRequest(key=key, kind="unknown", method="")
    method = str(payload.get("method", ""))
    params = payload.get("params")
    params = params if isinstance(params, Mapping) else {}
    raw_url = params.get("url")
    schema = params.get("requestedSchema")
    mode = params.get("mode")
    return McpInputRequest(
        key=key,
        kind=_KIND_BY_METHOD.get(method, "unknown"),
        method=method,
        message=san.sanitize(str(params.get("message", ""))),
        mode=str(mode) if mode is not None else None,
        url=san.sanitize(str(raw_url)) if raw_url is not None else None,
        requested_schema=schema if isinstance(schema, Mapping) else None,
    )


class DeclineAllInputHandler:
    """Default handler: decline every elicitation and audit the prompt hash."""

    def __init__(self, *, audit: AuditSink | None = None) -> None:
        self._audit = audit

    async def handle(
        self, request: McpInputRequest, *, server: str, org_id: str | None
    ) -> McpInputResponse:
        """Decline; audit ``{server, org_id, kind, mode, action, prompt_sha256}``."""
        payload: dict[str, object] = {
            "server": server,
            "org_id": org_id,
            "kind": request.kind,
            "mode": request.mode,
            "action": _DECLINE.action,
            "prompt_sha256": hashlib.sha256(request.message.encode("utf-8")).hexdigest(),
        }
        audit = self._audit
        if audit is None:
            from prismal.security.audit import AuditLogger

            audit = self._audit = AuditLogger()
        try:
            audit.log_event("mcp.input_request", payload)
        except Exception as exc:  # audit must never break the protocol loop
            logger.warning("mcp.input_request.audit_failed", error=str(exc))
        return _DECLINE


async def answer_input_requests(
    input_requests: Mapping[str, Any],
    *,
    handler: InputRequestHandler | None,
    server: str,
    org_id: str | None,
    stateless: bool = True,
    metrics: MetricsSink | None = None,
    sanitizer: InputSanitizer | None = None,
) -> dict[str, dict[str, Any]]:
    """Build ``inputResponses`` for every key; never raises (failures ⇒ ``decline``)."""
    sink = metrics if metrics is not None else default_metrics()
    resolved = handler if handler is not None else DeclineAllInputHandler()
    out: dict[str, dict[str, Any]] = {}
    for key, payload in input_requests.items():
        request = parse_input_request(str(key), payload, sanitizer=sanitizer)
        response = _DECLINE
        if request.kind == "elicitation" or (not stateless and request.kind != "unknown"):
            try:
                response = await resolved.handle(request, server=server, org_id=org_id)
            except Exception as exc:
                logger.warning("mcp.input_request.handler_failed", server=server, error=str(exc))
                response = _DECLINE
        sink.increment_counter(
            "mcp_input_requests", attributes={"kind": request.kind, "action": response.action}
        )
        out[str(key)] = response.to_wire()
    return out


__all__ = [
    "AuditSink",
    "DeclineAllInputHandler",
    "InputRequestHandler",
    "McpInputRequest",
    "McpInputResponse",
    "answer_input_requests",
    "parse_input_request",
]
