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

Phase PRT dual stack (SPEC-PRT-A2A-004, ADR-PRT-001 §2.1): when the host passes
``headers`` (and the auth-resolved ``org_id``), the request is negotiated
(:func:`~prismal.a2a.negotiation.negotiate_inbound`) and served by the v0.3 or
the v1 dispatch; ``headers=None`` keeps the pre-PRT behaviour byte-for-byte.
v1 payloads are decoded by :mod:`prismal.a2a.convert` and run through the same
``_run_task``. A v1 ``tenant`` that differs from ``org_id`` answers ``-32001``
(fail-closed, reveals nothing) and is audited ``a2a.inbound.tenant_mismatch``
hash-first; a refused legacy request answers ``-32009``. Tasks are indexed in an
:class:`~prismal.a2a.task_store.A2ATaskStorePort` for tenant-scoped
``GetTask``/``CancelTask``/``ListTasks``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from prismal.a2a import convert
from prismal.a2a.negotiation import count_a2a_request, negotiate_inbound, normalize_version
from prismal.a2a.task_store import (
    A2ATaskStorePort,
    InMemoryA2ATaskStore,
    TaskRecord,
    TaskStoreQueryError,
)
from prismal.a2a.types import A2AArtifact, A2AMessage, A2APart, A2ATask
from prismal.a2a.v1 import constants as c
from prismal.a2a.v1.types import (
    CancelTaskRequest,
    GetTaskRequest,
    ListTasksRequest,
    ListTasksResult,
    SendMessageRequest,
    SendMessageResponse,
    StreamResponse,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatusUpdateEvent,
    TaskStatusV1,
)
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Mapping

    from pydantic import BaseModel

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


def _wire(model: BaseModel) -> dict[str, Any]:
    """v1 wire dump: camelCase, no nulls, JSON-safe."""
    return model.model_dump(by_alias=True, exclude_none=True, mode="json")


def _hash(value: str | None) -> str | None:
    """Hash-first audit identifier (never the raw tenant)."""
    if value is None:
        return None
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def _now() -> datetime:
    return datetime.now(UTC)


class _V1Error(Exception):
    """Internal: a v1 JSON-RPC error to return to the caller."""

    def __init__(self, code: int, message: str, outcome: str = "error") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.outcome = outcome


@dataclass(frozen=True)
class _Scope:
    """Tenant/principal scope of one negotiated request (new path only)."""

    org_id: str | None
    principal: str | None


class A2AServerHandler:
    """Maps A2A JSON-RPC requests onto the prismal graph (SPEC-A2A-003)."""

    def __init__(
        self,
        graph: Any,
        *,
        settings: Settings | None = None,
        audit: Any | None = None,
        sanitizer: Any | None = None,
        task_store: A2ATaskStorePort | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._graph = graph
        self._settings = settings
        self._audit = audit
        self._sanitizer = sanitizer
        self._tasks: dict[str, A2ATask] = {}
        self._task_store: A2ATaskStorePort = (
            task_store
            if task_store is not None
            else InMemoryA2ATaskStore(self._tasks, max_page_size=self._max_page_size())
        )
        self._clock = clock or _now
        # Serializes check-then-reserve of caller-supplied task ids (no TOCTOU).
        self._claim_lock = asyncio.Lock()

    # ── helpers ────────────────────────────────────────────────────────────

    def _max_page_size(self) -> int:
        configured = (
            int(self._settings.a2a_tasks_list_max_page_size)
            if self._settings is not None
            else c.LIST_TASKS_MAX_PAGE_SIZE
        )
        return min(configured, c.LIST_TASKS_MAX_PAGE_SIZE)

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

    @staticmethod
    def _thread_id(task_id: str, scope: _Scope | None) -> str:
        """Graph thread for *task_id*: owner-namespaced on the scoped path.

        ``scope=None`` (pre-PRT host) keeps the plain task id byte-for-byte; a
        scoped request never shares a checkpoint thread with another
        tenant/principal that reuses the same id (nor resumes one after the
        in-memory store forgets the owner).
        """
        if scope is None:
            return task_id
        key = f"{scope.org_id or ''}|{scope.principal or ''}|{task_id}"
        return hashlib.sha256(key.encode()).hexdigest()

    async def _invoke(self, state: dict[str, Any], task_id: str, scope: _Scope | None) -> Any:
        config = {"configurable": {"thread_id": self._thread_id(task_id, scope)}}
        if scope is None:
            return await self._graph.ainvoke(state, config)
        from prismal.monitoring.host_stats import use_org

        try:
            with use_org(scope.org_id):
                return await self._graph.ainvoke(state, config)
        except Exception:
            self._update_state(task_id, scope, TaskState.FAILED)
            raise

    async def _run_task(
        self,
        message_text: str,
        *,
        skill_id: str | None,
        task_id: str,
        scope: _Scope | None = None,
    ) -> AsyncIterator[A2AArtifact]:
        """Sanitize → invoke graph(thread of task_id) → yield artifacts → audit."""
        from langchain_core.messages import AIMessage, HumanMessage

        clean = self._get_sanitizer().sanitize(message_text)
        state: dict[str, Any] = {
            "messages": [HumanMessage(content=clean)],
            "metadata": {"a2a": {"inbound": True, "skill": skill_id}},
        }
        result = await self._invoke(state, task_id, scope)

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

    async def _aggregate_send(
        self, params: dict[str, Any], *, task_id: str, scope: _Scope | None = None
    ) -> A2ATask:
        skill_id = params.get("skillId")
        text = self._message_text(params)
        artifacts = [
            a async for a in self._run_task(text, skill_id=skill_id, task_id=task_id, scope=scope)
        ]
        incoming = A2AMessage(
            role="user", parts=[A2APart(kind="text", text=text)], message_id=uuid.uuid4().hex
        )
        task = A2ATask(id=task_id, status="completed", history=[incoming], artifacts=artifacts)
        self._tasks[task_id] = task
        return task

    # ── dispatch ─────────────────────────────────────────────────────────────

    async def handle_rpc(
        self,
        request: dict[str, Any],
        *,
        auth_ctx: AuthContext | None = None,
        headers: Mapping[str, str] | None = None,
        org_id: str | None = None,
    ) -> dict[str, Any]:
        """Dispatch a JSON-RPC request (non-streaming).

        ``headers=None`` ⇒ the pre-PRT v0.3 dispatch, unchanged. Otherwise the
        request is negotiated and served by the v0.3 or v1 stack, scoped to the
        host-resolved ``org_id``.
        """
        if headers is None:
            return await self._handle_legacy(request, auth_ctx)
        method = str(request.get("method"))
        req_id = request.get("id")
        version = negotiate_inbound(request, headers, settings=self._settings)
        label = version or self._header_label(headers)
        if not self._authorized(auth_ctx):
            self._get_audit().log_event("a2a.inbound", {"status": "unauthorized", "method": method})
            self._count(label, method, "unauthorized")
            return _jsonrpc_error(req_id, _UNAUTHORIZED, "authentication required")
        if version is None:
            self._count(label, method, "rejected")
            return _jsonrpc_error(req_id, c.ERR_VERSION_NOT_SUPPORTED, "A2A version not supported")
        scope = _Scope(org_id=org_id, principal=auth_ctx.subject if auth_ctx else None)
        if version == "0.3":
            resp = await self._handle_legacy(request, auth_ctx, scope=scope)
            self._count(version, method, "error" if "error" in resp else "ok")
            return resp
        try:
            result = await self._dispatch_v1(method, request.get("params"), scope)
        except _V1Error as err:
            self._count(version, method, err.outcome)
            return _jsonrpc_error(req_id, err.code, err.message)
        self._count(version, method, "ok")
        return _jsonrpc_result(req_id, result)

    async def _handle_legacy(
        self,
        request: dict[str, Any],
        auth_ctx: AuthContext | None,
        *,
        scope: _Scope | None = None,
    ) -> dict[str, Any]:
        """The pre-PRT v0.3 dispatch (``scope`` set ⇒ tenant-scoped task lookups)."""
        req_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}

        if not self._authorized(auth_ctx):
            self._get_audit().log_event("a2a.inbound", {"status": "unauthorized", "method": method})
            return _jsonrpc_error(req_id, _UNAUTHORIZED, "authentication required")

        if method == "message/send":
            task_id, refused = await self._claim_legacy_send(params, scope, method)
            if refused is not None:
                return _jsonrpc_error(req_id, refused["code"], refused["message"])
            task = await self._aggregate_send(params, task_id=task_id, scope=scope)
            if scope is not None:
                self._record(task, context_id=task.id, scope=scope)
            return _jsonrpc_result(req_id, task.model_dump(by_alias=True))

        if method == "tasks/get":
            stored = self._tasks.get(params.get("id", ""))
            if stored is not None and not self._in_scope(stored.id, scope):
                stored = None
            if stored is None:
                return _jsonrpc_error(req_id, _TASK_NOT_FOUND, "task not found")
            return _jsonrpc_result(req_id, stored.model_dump(by_alias=True))

        if method == "tasks/cancel":
            task_id = params.get("id", "")
            stored = self._tasks.get(task_id)
            if stored is not None and not self._in_scope(task_id, scope):
                stored = None
            if stored is None:
                return _jsonrpc_error(req_id, _TASK_NOT_FOUND, "task not found")
            canceled = stored.model_copy(update={"status": "canceled"})
            self._tasks[task_id] = canceled
            self._update_state(task_id, scope, TaskState.CANCELED)
            self._get_audit().log_event("a2a.inbound", {"task_id": task_id, "status": "canceled"})
            return _jsonrpc_result(req_id, canceled.model_dump(by_alias=True))

        return _jsonrpc_error(req_id, _METHOD_NOT_FOUND, f"unknown method: {method}")

    async def stream_rpc(
        self,
        request: dict[str, Any],
        *,
        auth_ctx: AuthContext | None = None,
        headers: Mapping[str, str] | None = None,
        org_id: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream a send as SSE ``data:`` lines (the host mounts this).

        ``headers=None`` ⇒ the pre-PRT v0.3 stream, unchanged; otherwise the
        request is negotiated like :meth:`handle_rpc`.
        """
        if headers is None:
            async for line in self._stream_legacy(request, auth_ctx):
                yield line
            return
        method = str(request.get("method"))
        req_id = request.get("id")
        version = negotiate_inbound(request, headers, settings=self._settings)
        label = version or self._header_label(headers)
        if not self._authorized(auth_ctx):
            self._get_audit().log_event("a2a.inbound", {"status": "unauthorized", "method": method})
            self._count(label, method, "unauthorized")
            yield _sse(_jsonrpc_error(req_id, _UNAUTHORIZED, "authentication required"))
            return
        if version is None:
            self._count(label, method, "rejected")
            yield _sse(
                _jsonrpc_error(req_id, c.ERR_VERSION_NOT_SUPPORTED, "A2A version not supported")
            )
            return
        scope = _Scope(org_id=org_id, principal=auth_ctx.subject if auth_ctx else None)
        if version == "0.3":
            async for line in self._stream_legacy(request, auth_ctx, scope=scope):
                yield line
            self._count(version, method, "ok")
            return
        try:
            if method != c.METHOD_SEND_STREAMING_MESSAGE:
                raise _V1Error(c.ERR_UNSUPPORTED_OPERATION, "streaming unsupported for method")
            params = self._v1_params(request.get("params"), scope, method)
            send = self._validate(SendMessageRequest, params)
            task_id, context_id = await self._reserve_v1(send, scope, method)
        except _V1Error as err:
            self._count(version, method, err.outcome)
            yield _sse(_jsonrpc_error(req_id, err.code, err.message))
            return
        async for event in self._v1_stream(send, scope, task_id, context_id):
            yield _sse(_jsonrpc_result(req_id, _wire(event)))
        self._count(version, method, "ok")

    async def _stream_legacy(
        self,
        request: dict[str, Any],
        auth_ctx: AuthContext | None,
        *,
        scope: _Scope | None = None,
    ) -> AsyncIterator[str]:
        """The pre-PRT v0.3 stream."""
        req_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}

        if not self._authorized(auth_ctx):
            yield _sse(_jsonrpc_error(req_id, _UNAUTHORIZED, "authentication required"))
            return
        if method not in ("message/send", "message/stream"):
            yield _sse(_jsonrpc_error(req_id, _METHOD_NOT_FOUND, "streaming unsupported"))
            return
        task_id, refused = await self._claim_legacy_send(params, scope, str(method))
        if refused is not None:
            yield _sse(_jsonrpc_error(req_id, refused["code"], refused["message"]))
            return

        skill_id = params.get("skillId")
        text = self._message_text(params)
        artifacts: list[A2AArtifact] = []
        async for artifact in self._run_task(text, skill_id=skill_id, task_id=task_id, scope=scope):
            artifacts.append(artifact)
            yield _sse(
                _jsonrpc_result(
                    req_id, {"kind": "artifact", "artifact": artifact.model_dump(by_alias=True)}
                )
            )

        self._tasks[task_id] = A2ATask(id=task_id, status="completed", artifacts=artifacts)
        if scope is not None:
            self._record(self._tasks[task_id], context_id=task_id, scope=scope)
        yield _sse(_jsonrpc_result(req_id, {"kind": "status", "status": "completed"}))

    # ── PRT: shared helpers ──────────────────────────────────────────────────

    @staticmethod
    def _header_label(headers: Mapping[str, str]) -> str:
        """Metric label for a refused request: the normalized header or ``"0.3"``."""
        for key, value in headers.items():
            if key.lower() == c.VERSION_HEADER.lower() and isinstance(value, str):
                return normalize_version(value) or "unsupported"
        return c.A2A_LEGACY_PROTOCOL_VERSION

    @staticmethod
    def _count(version: str, method: str, outcome: str) -> None:
        count_a2a_request(
            protocol_version=version, direction="inbound", method=method, outcome=outcome
        )

    def _record(self, task: A2ATask, *, context_id: str, scope: _Scope) -> TaskRecord:
        now = self._clock()
        existing = self._task_store.peek(task.id)
        record = TaskRecord(
            task_id=task.id,
            context_id=context_id,
            org_id=scope.org_id,
            principal=scope.principal,
            state=convert.state_to_v1(task.status),
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
            artifact_ids=tuple(a.artifact_id for a in task.artifacts),
        )
        self._task_store.put(record)
        return record

    def _in_scope(self, task_id: str, scope: _Scope | None) -> bool:
        """Legacy lookups: the tenant must own it.

        ``scope=None`` (pre-PRT, unscoped) only reaches tasks an unscoped call
        created: a task a scoped request recorded has an owner in the task store
        and is treated as not found, so an unscoped get/cancel can never read or
        mutate another tenant's task.
        """
        if scope is None:
            return self._task_store.peek(task_id) is None
        try:
            self._scoped_record(task_id, scope)
        except _V1Error:
            return False
        return True

    def _update_state(self, task_id: str, scope: _Scope | None, state: TaskState) -> None:
        if scope is None:
            return
        record = self._task_store.get(task_id, org_id=scope.org_id)
        if record is not None:
            self._task_store.put(replace(record, state=state, updated_at=self._clock()))

    def _claim_task_id(self, task_id: str | None, scope: _Scope | None, method: str) -> None:
        """Refuse a caller-supplied task id the caller does not own (fail-closed).

        An existing task (in the store or the registry) may be continued only by
        the same ``org_id`` **and** principal; anything else — another tenant,
        another principal, an unscoped caller touching a tenant's task, or a
        scoped caller touching an unrecorded task — answers ``-32001`` (no leak)
        and is audited ``a2a.inbound.tenant_mismatch``. Never overwrites.
        """
        if not task_id:
            return
        owner = self._task_store.peek(task_id)
        if owner is None and task_id not in self._tasks:
            return
        owned = (
            owner is not None
            and scope is not None
            and owner.org_id == scope.org_id
            and owner.principal == scope.principal
        )
        if owned:
            return
        self._get_audit().log_event(
            "a2a.inbound.tenant_mismatch",
            {
                "method": method,
                "reason": "task_id_not_owned",
                "task_hash": _hash(task_id),
                "org_hash": _hash(scope.org_id) if scope is not None else None,
            },
        )
        raise _V1Error(c.ERR_TASK_NOT_FOUND, "task not found", outcome="tenant_mismatch")

    async def _reserve(
        self, task_id: str, context_id: str, scope: _Scope, method: str, *, v1: bool = False
    ) -> None:
        """Atomically claim *task_id* and record it ``WORKING`` before the graph runs.

        Check and put happen under one lock, so two concurrent sends reusing an
        id cannot both pass the ownership check (the loser gets ``-32001``).
        ``v1`` also refuses continuing a terminal task.
        """
        async with self._claim_lock:
            self._claim_task_id(task_id, scope, method)
            existing = self._task_store.peek(task_id)
            if v1 and existing is not None and existing.state.value in c.TERMINAL_STATES:
                raise _V1Error(c.ERR_UNSUPPORTED_OPERATION, "task is in a terminal state")
            now = self._clock()
            self._task_store.put(
                TaskRecord(
                    task_id=task_id,
                    context_id=context_id,
                    org_id=scope.org_id,
                    principal=scope.principal,
                    state=TaskState.WORKING,
                    created_at=existing.created_at if existing is not None else now,
                    updated_at=now,
                    artifact_ids=existing.artifact_ids if existing is not None else (),
                )
            )

    async def _claim_legacy_send(
        self, params: dict[str, Any], scope: _Scope | None, method: str
    ) -> tuple[str, dict[str, Any] | None]:
        """Resolve the legacy send's task id and claim it (scoped ⇒ reserved atomically)."""
        task_id = params.get("taskId") or uuid.uuid4().hex
        if scope is None:
            return task_id, self._claim_legacy(params, scope, method)
        try:
            await self._reserve(task_id, task_id, scope, method)
        except _V1Error as err:
            return task_id, {"code": err.code, "message": err.message}
        return task_id, None

    def _claim_legacy(
        self, params: dict[str, Any], scope: _Scope | None, method: str
    ) -> dict[str, Any] | None:
        """Legacy send/stream guard: ``None`` when allowed, else the JSON-RPC error body.

        Unscoped (pre-PRT) callers only trip it on a task a scoped request recorded.
        """
        task_id = params.get("taskId")
        if not isinstance(task_id, str) or not task_id:
            return None
        if scope is None and self._task_store.peek(task_id) is None:
            return None  # pre-PRT behaviour for unrecorded tasks
        try:
            self._claim_task_id(task_id, scope, method)
        except _V1Error as err:
            return {"code": err.code, "message": err.message}
        return None

    # ── PRT: v1 dispatch ─────────────────────────────────────────────────────

    def _v1_params(self, params: Any, scope: _Scope, method: str) -> dict[str, Any]:
        """Validate the params shape and enforce the tenant rule (fail-closed)."""
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise _V1Error(c.ERR_INVALID_PARAMS, "params must be an object")
        tenant = params.get("tenant")
        if tenant is not None and tenant != scope.org_id:
            self._get_audit().log_event(
                "a2a.inbound.tenant_mismatch",
                {
                    "method": method,
                    "tenant_hash": _hash(str(tenant)),
                    "org_hash": _hash(scope.org_id),
                },
            )
            raise _V1Error(c.ERR_TASK_NOT_FOUND, "task not found", outcome="tenant_mismatch")
        return params

    @staticmethod
    def _validate(model: type[Any], params: dict[str, Any]) -> Any:
        try:
            return model.model_validate(params)
        except ValidationError as exc:
            raise _V1Error(c.ERR_INVALID_PARAMS, "invalid params") from exc

    def _principal_filter(self, scope: _Scope) -> str | None:
        return scope.principal if self._strict() else None

    def _scoped_record(self, task_id: str, scope: _Scope) -> TaskRecord:
        record = self._task_store.get(task_id, org_id=scope.org_id)
        principal = self._principal_filter(scope)
        if record is None or (principal is not None and record.principal != principal):
            raise _V1Error(c.ERR_TASK_NOT_FOUND, "task not found")
        return record

    def _task_body(self, record: TaskRecord) -> A2ATask:
        body = self._task_store.task(record.task_id)
        if body is None:
            body = A2ATask(id=record.task_id, status=convert.state_to_internal(record.state))
        return body

    async def _dispatch_v1(self, method: str, raw_params: Any, scope: _Scope) -> Any:
        params = self._v1_params(raw_params, scope, method)
        if method == c.METHOD_SEND_MESSAGE:
            send = self._validate(SendMessageRequest, params)
            task_id, context_id = await self._reserve_v1(send, scope, method)
            return await self._v1_send(send, scope, task_id, context_id)
        if method == c.METHOD_GET_TASK:
            get = self._validate(GetTaskRequest, params)
            record = self._scoped_record(get.id, scope)
            return _wire(
                convert.task_from_internal(
                    self._task_body(record),
                    context_id=record.context_id,
                    timestamp=record.updated_at.isoformat(),
                    history_length=get.history_length,
                )
            )
        if method == c.METHOD_CANCEL_TASK:
            return self._v1_cancel(self._validate(CancelTaskRequest, params), scope)
        if method == c.METHOD_LIST_TASKS:
            return self._v1_list(self._validate(ListTasksRequest, params), scope)
        if method in c.V1_METHODS:
            raise _V1Error(c.ERR_UNSUPPORTED_OPERATION, f"unsupported operation: {method}")
        raise _V1Error(_METHOD_NOT_FOUND, f"unknown method: {method}")

    async def _reserve_v1(
        self, send: SendMessageRequest, scope: _Scope, method: str
    ) -> tuple[str, str]:
        """Ownership + no terminal continuation, reserved atomically → ``(task_id, context_id)``."""
        task_id = send.message.task_id or uuid.uuid4().hex
        context_id = send.message.context_id or uuid.uuid4().hex
        await self._reserve(task_id, context_id, scope, method, v1=True)
        return task_id, context_id

    async def _v1_run(
        self, send: SendMessageRequest, scope: _Scope, task_id: str, context_id: str
    ) -> tuple[A2ATask, list[A2AArtifact]]:
        skill_id = (send.metadata or {}).get("skillId")
        text = convert.message_text(send.message)
        artifacts = [
            a async for a in self._run_task(text, skill_id=skill_id, task_id=task_id, scope=scope)
        ]
        task = A2ATask(
            id=task_id,
            status="completed",
            history=[convert.message_to_internal(send.message)],
            artifacts=artifacts,
        )
        self._tasks[task_id] = task
        self._record(task, context_id=context_id, scope=scope)
        return task, artifacts

    async def _v1_send(
        self, send: SendMessageRequest, scope: _Scope, task_id: str, context_id: str
    ) -> dict[str, Any]:
        task, _ = await self._v1_run(send, scope, task_id, context_id)
        history_length = send.configuration.history_length if send.configuration else None
        v1_task = convert.task_from_internal(
            task,
            context_id=context_id,
            timestamp=self._clock().isoformat(),
            history_length=history_length,
        )
        return _wire(SendMessageResponse(task=v1_task))

    async def _v1_stream(
        self, send: SendMessageRequest, scope: _Scope, task_id: str, context_id: str
    ) -> AsyncIterator[StreamResponse]:
        send = send.model_copy(
            update={
                "message": send.message.model_copy(
                    update={"task_id": task_id, "context_id": context_id}
                )
            }
        )
        yield StreamResponse(
            status_update=TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                status=TaskStatusV1(state=TaskState.WORKING, timestamp=self._clock().isoformat()),
            )
        )
        _, artifacts = await self._v1_run(send, scope, task_id, context_id)
        for artifact in artifacts:
            yield StreamResponse(
                artifact_update=TaskArtifactUpdateEvent(
                    task_id=task_id,
                    context_id=context_id,
                    artifact=convert.artifact_from_internal(artifact),
                    last_chunk=True,
                )
            )
        yield StreamResponse(
            status_update=TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                status=TaskStatusV1(state=TaskState.COMPLETED, timestamp=self._clock().isoformat()),
            )
        )

    def _v1_cancel(self, cancel: CancelTaskRequest, scope: _Scope) -> dict[str, Any]:
        record = self._scoped_record(cancel.id, scope)
        if record.state.value in c.TERMINAL_STATES:
            raise _V1Error(c.ERR_TASK_NOT_CANCELABLE, "task not cancelable")
        body = self._task_body(record).model_copy(update={"status": "canceled"})
        self._tasks[record.task_id] = body
        updated = replace(record, state=TaskState.CANCELED, updated_at=self._clock())
        self._task_store.put(updated)
        self._get_audit().log_event(
            "a2a.inbound", {"task_id": record.task_id, "status": "canceled"}
        )
        return _wire(
            convert.task_from_internal(
                body, context_id=record.context_id, timestamp=updated.updated_at.isoformat()
            )
        )

    def _v1_list(self, params: ListTasksRequest, scope: _Scope) -> dict[str, Any]:
        principal = self._principal_filter(scope)
        try:
            records, token = self._task_store.list(
                org_id=scope.org_id, params=params, principal=principal
            )
            total = self._task_store.count(org_id=scope.org_id, params=params, principal=principal)
        except TaskStoreQueryError as exc:
            raise _V1Error(c.ERR_INVALID_PARAMS, str(exc)) from exc
        page_size = min(params.page_size or c.LIST_TASKS_DEFAULT_PAGE_SIZE, self._max_page_size())
        tasks = [
            convert.task_from_internal(
                self._task_body(r),
                context_id=r.context_id,
                timestamp=r.updated_at.isoformat(),
                include_artifacts=bool(params.include_artifacts),
                history_length=params.history_length,
            )
            for r in records
        ]
        return _wire(
            ListTasksResult(
                tasks=tasks, next_page_token=token, page_size=page_size, total_size=total
            )
        )


__all__ = ["A2AServerHandler", "AuthContext"]
