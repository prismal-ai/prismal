"""Inbound A2A — server handler (Phase I — SPEC-A2A-003).

Exposes the prismal compiled graph as an A2A agent. The handler is **framework
agnostic**: the host (``prismal-server``) mounts the HTTP routes
(``GET /.well-known/agent-card.json`` and ``POST /a2a``) and forwards the parsed
JSON-RPC request here, after validating the caller's auth.

Dispatches ``message/send``, ``tasks/get`` and ``tasks/cancel``. Incoming
message text is **untrusted** and is sanitized
(:class:`~prismal.security.InputSanitizer`) before it reaches the graph; every
task is audited (``a2a.inbound``) without content.

Two entry points:

- :meth:`handle_rpc` — request/response (non-streaming). ``message/send`` runs
  the graph and returns the completed :class:`~prismal.a2a.types.A2ATask`.
- :meth:`stream_rpc` — async generator of SSE ``data:`` lines for
  ``message/send`` (the path the host streams to the caller).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from prismal.a2a.types import A2AArtifact, A2AMessage, A2APart, A2ATask
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from prismal.core.config import Settings

logger = get_logger(__name__)

# JSON-RPC error codes (subset).
_METHOD_NOT_FOUND = -32601
_UNAUTHORIZED = -32001
_TASK_NOT_FOUND = -32004


@dataclass(frozen=True)
class AuthContext:
    """Caller identity passed by the host after it validates inbound auth."""

    authenticated: bool = False
    subject: str | None = None
    did: str | None = None


def _jsonrpc_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _jsonrpc_result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _sse(payload: dict[str, Any]) -> str:
    """Format a JSON-RPC payload as an SSE ``data:`` line."""
    return f"data: {json.dumps(payload)}\n\n"


class A2AServerHandler:
    """Maps A2A JSON-RPC requests onto the prismal graph (SPEC-A2A-003)."""

    def __init__(
        self,
        graph: Any,
        *,
        settings: Settings | None = None,
        audit: Any | None = None,
        sanitizer: Any | None = None,
    ) -> None:
        self._graph = graph
        self._settings = settings
        self._audit = audit
        self._sanitizer = sanitizer
        self._tasks: dict[str, A2ATask] = {}

    # ── helpers ────────────────────────────────────────────────────────────

    def _strict(self) -> bool:
        return bool(self._settings.a2a_strict) if self._settings is not None else False

    def _authorized(self, auth_ctx: AuthContext | None) -> bool:
        if not self._strict():
            return True
        return auth_ctx is not None and auth_ctx.authenticated

    def _get_sanitizer(self) -> Any:
        if self._sanitizer is None:
            from prismal.security import InputSanitizer

            self._sanitizer = InputSanitizer()
        return self._sanitizer

    def _get_audit(self) -> Any:
        if self._audit is None:
            from prismal.security import AuditLogger

            self._audit = AuditLogger()
        return self._audit

    @staticmethod
    def _message_text(params: dict[str, Any]) -> str:
        message = params.get("message") or {}
        parts = message.get("parts") or []
        return "\n".join(
            p.get("text", "") for p in parts if isinstance(p, dict) and p.get("kind") == "text"
        )

    # ── task execution ──────────────────────────────────────────────────────

    async def _run_task(
        self, message_text: str, *, skill_id: str | None, task_id: str
    ) -> AsyncIterator[A2AArtifact]:
        """Sanitize → invoke graph(thread=task_id) → yield artifacts → audit."""
        from langchain_core.messages import AIMessage, HumanMessage

        clean = self._get_sanitizer().sanitize(message_text)
        state: dict[str, Any] = {
            "messages": [HumanMessage(content=clean)],
            "metadata": {"a2a": {"inbound": True, "skill": skill_id}},
        }
        config = {"configurable": {"thread_id": task_id}}
        result = await self._graph.ainvoke(state, config)

        answer = ""
        for msg in reversed(result.get("messages", [])):
            if isinstance(msg, AIMessage) and isinstance(msg.content, str):
                answer = msg.content
                break

        self._get_audit().log_event(
            "a2a.inbound",
            {"task_id": task_id, "skill": skill_id, "status": "completed"},
        )
        yield A2AArtifact(
            artifact_id=uuid.uuid4().hex,
            parts=[A2APart(kind="text", text=answer)],
        )

    async def _aggregate_send(self, params: dict[str, Any]) -> A2ATask:
        skill_id = params.get("skillId")
        task_id = params.get("taskId") or uuid.uuid4().hex
        text = self._message_text(params)
        artifacts = [a async for a in self._run_task(text, skill_id=skill_id, task_id=task_id)]
        incoming = A2AMessage(
            role="user", parts=[A2APart(kind="text", text=text)], message_id=uuid.uuid4().hex
        )
        task = A2ATask(id=task_id, status="completed", history=[incoming], artifacts=artifacts)
        self._tasks[task_id] = task
        return task

    # ── dispatch ─────────────────────────────────────────────────────────────

    async def handle_rpc(
        self, request: dict[str, Any], *, auth_ctx: AuthContext | None = None
    ) -> dict[str, Any]:
        """Dispatch a JSON-RPC request (non-streaming)."""
        req_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}

        if not self._authorized(auth_ctx):
            self._get_audit().log_event("a2a.inbound", {"status": "unauthorized", "method": method})
            return _jsonrpc_error(req_id, _UNAUTHORIZED, "authentication required")

        if method == "message/send":
            task = await self._aggregate_send(params)
            return _jsonrpc_result(req_id, task.model_dump(by_alias=True))

        if method == "tasks/get":
            stored = self._tasks.get(params.get("id", ""))
            if stored is None:
                return _jsonrpc_error(req_id, _TASK_NOT_FOUND, "task not found")
            return _jsonrpc_result(req_id, stored.model_dump(by_alias=True))

        if method == "tasks/cancel":
            task_id = params.get("id", "")
            stored = self._tasks.get(task_id)
            if stored is None:
                return _jsonrpc_error(req_id, _TASK_NOT_FOUND, "task not found")
            canceled = stored.model_copy(update={"status": "canceled"})
            self._tasks[task_id] = canceled
            self._get_audit().log_event("a2a.inbound", {"task_id": task_id, "status": "canceled"})
            return _jsonrpc_result(req_id, canceled.model_dump(by_alias=True))

        return _jsonrpc_error(req_id, _METHOD_NOT_FOUND, f"unknown method: {method}")

    async def stream_rpc(
        self, request: dict[str, Any], *, auth_ctx: AuthContext | None = None
    ) -> AsyncIterator[str]:
        """Stream ``message/send`` as SSE ``data:`` lines (the host mounts this)."""
        req_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}

        if not self._authorized(auth_ctx):
            yield _sse(_jsonrpc_error(req_id, _UNAUTHORIZED, "authentication required"))
            return
        if method not in ("message/send", "message/stream"):
            yield _sse(_jsonrpc_error(req_id, _METHOD_NOT_FOUND, "streaming unsupported"))
            return

        skill_id = params.get("skillId")
        task_id = params.get("taskId") or uuid.uuid4().hex
        text = self._message_text(params)
        artifacts: list[A2AArtifact] = []
        async for artifact in self._run_task(text, skill_id=skill_id, task_id=task_id):
            artifacts.append(artifact)
            yield _sse(
                _jsonrpc_result(
                    req_id, {"kind": "artifact", "artifact": artifact.model_dump(by_alias=True)}
                )
            )

        self._tasks[task_id] = A2ATask(id=task_id, status="completed", artifacts=artifacts)
        yield _sse(_jsonrpc_result(req_id, {"kind": "status", "status": "completed"}))


__all__ = ["A2AServerHandler", "AuthContext"]
