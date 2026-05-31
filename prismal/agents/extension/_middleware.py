"""Internal middleware chain for ``@prismal_node`` (SPEC-EXT-007).

Internal module (prefixed ``_``): not part of the public extension API.
Each middleware has the signature ``async (next_fn, state, metadata) -> dict``
and wraps the next callable. :func:`build_pipeline` composes the stack so
that ``DEFAULT_MIDDLEWARE_STACK[0]`` is the outermost layer.

The ``_tool_call_checker`` module attribute is a seam: tests override it to
observe ``strict`` security behaviour without touching the permission DB.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, cast

import structlog

from prismal.core.exceptions import NodeExecutionError, NodeTimeoutError
from prismal.monitoring.otel import OTelManager

if TYPE_CHECKING:
    from prismal.agents.extension.decorators import NodeFn, NodeMetadata
    from prismal.agents.state import AgentState

Middleware = Callable[
    ["NodeFn", "AgentState", "NodeMetadata"],
    Awaitable[dict[str, Any]],
]
"""A middleware receives ``(next_fn, state, metadata)`` and returns a state_update."""

_logger = structlog.get_logger("prismal.ext.node")
_audit_logger_singleton: Any | None = None


def _get_audit_logger() -> Any:
    """Return a process-wide :class:`AuditLogger`, created on first use."""
    global _audit_logger_singleton
    if _audit_logger_singleton is None:
        from prismal.security.audit import AuditLogger

        _audit_logger_singleton = AuditLogger()
    return _audit_logger_singleton


def _state_keys(state: AgentState) -> list[str]:
    return list(state.keys())


def _hash_update(update: dict[str, Any]) -> str:
    """Return a sha256 hex digest of a state_update (best-effort, never raises)."""
    try:
        payload = json.dumps(update, default=str, sort_keys=True)
    except (TypeError, ValueError):
        payload = repr(update)
    return hashlib.sha256(payload.encode()).hexdigest()


# ── Security ──────────────────────────────────────────────────────────────────


def _sanitize_state(state: AgentState) -> AgentState:
    """Return a shallow copy of ``state`` with the last human message sanitized."""
    from langchain_core.messages import HumanMessage

    from prismal.security.sanitizer import InputSanitizer

    messages = list(state.get("messages") or [])
    sanitizer = InputSanitizer()
    for idx in range(len(messages) - 1, -1, -1):
        msg = messages[idx]
        if isinstance(msg, HumanMessage) and isinstance(msg.content, str):
            messages[idx] = HumanMessage(content=sanitizer.sanitize(msg.content))
            break
    new_state = dict(state)
    new_state["messages"] = messages
    return cast("AgentState", new_state)


async def _check_tool_calls(update: dict[str, Any], _metadata: NodeMetadata) -> None:
    """Enforce permissions on any tool_calls present in the node output.

    Tools absent from ``_TOOL_PERMISSION_MAP`` pass through. Mapped tools
    require a non-expired grant or a :class:`PermissionDeniedError` is raised.
    """
    from prismal.core.exceptions import PermissionDeniedError
    from prismal.security.action_interceptor import _TOOL_PERMISSION_MAP
    from prismal.security.permissions import PermissionManager

    messages = update.get("messages") or []
    manager: PermissionManager | None = None
    for msg in messages:
        for call in getattr(msg, "tool_calls", None) or []:
            tool_name = call.get("name") if isinstance(call, dict) else getattr(call, "name", None)
            if not tool_name:
                continue
            required = _TOOL_PERMISSION_MAP.get(tool_name)
            if required is None:
                continue
            if manager is None:
                manager = PermissionManager()
            if not await manager.check(required, "*"):
                raise PermissionDeniedError(tool_name, required.value)


# Seam: overridable by tests / advanced operators.
_tool_call_checker: Callable[[dict[str, Any], NodeMetadata], Awaitable[None]] = _check_tool_calls


async def security_middleware(
    next_fn: NodeFn, state: AgentState, metadata: NodeMetadata
) -> dict[str, Any]:
    """Sanitize input (standard/strict) and check tool_calls (strict)."""
    if metadata.security == "off":
        return await next_fn(state)
    update = await next_fn(_sanitize_state(state))
    if metadata.security == "strict":
        await _tool_call_checker(update, metadata)
    return update


# ── Observability ───────────────────────────────────────────────────────────


async def otel_middleware(
    next_fn: NodeFn, state: AgentState, metadata: NodeMetadata
) -> dict[str, Any]:
    """Wrap execution in an OTel span named ``prismal.ext.node.<name>``."""
    otel = OTelManager()
    with otel.start_span(f"prismal.ext.node.{metadata.name}") as span:
        span.set_attribute("prismal.node", metadata.name)
        return await next_fn(state)


async def logger_middleware(
    next_fn: NodeFn, state: AgentState, metadata: NodeMetadata
) -> dict[str, Any]:
    """Bind contextual fields to structlog for the duration of the node."""
    log = _logger.bind(
        node_name=metadata.name,
        session_id=state.get("session_id", ""),
        capabilities=list(metadata.capabilities),
    )
    log.debug("ext.node.start")
    return await next_fn(state)


async def audit_middleware(
    next_fn: NodeFn, state: AgentState, metadata: NodeMetadata
) -> dict[str, Any]:
    """Record node execution (status + duration + state hash) to the audit log."""
    if not metadata.audit:
        return await next_fn(state)
    session_id = str(state.get("session_id", ""))
    start = time.monotonic()
    try:
        update = await next_fn(state)
    except BaseException:
        duration_ms = (time.monotonic() - start) * 1000.0
        _get_audit_logger().log_node(
            node_name=metadata.name,
            session_id=session_id,
            status="error",
            state_hash="",
            duration_ms=duration_ms,
        )
        raise
    duration_ms = (time.monotonic() - start) * 1000.0
    _get_audit_logger().log_node(
        node_name=metadata.name,
        session_id=session_id,
        status="ok",
        state_hash=_hash_update(update),
        duration_ms=duration_ms,
    )
    return update


# ── Resilience ────────────────────────────────────────────────────────────────


async def retry_middleware(
    next_fn: NodeFn, state: AgentState, metadata: NodeMetadata
) -> dict[str, Any]:
    """Retry the node on configured exceptions with bounded backoff."""
    policy = metadata.retry
    if policy is None:
        return await next_fn(state)
    last_exc: BaseException | None = None
    for attempt in range(policy.max_attempts):
        try:
            return await next_fn(state)
        except policy.retry_on as exc:
            last_exc = exc
            if attempt >= policy.max_attempts - 1:
                raise
            if policy.backoff_s:
                delay = policy.backoff_s[min(attempt, len(policy.backoff_s) - 1)]
                if delay > 0:
                    await asyncio.sleep(delay)
    assert last_exc is not None  # pragma: no cover - loop always returns or raises
    raise last_exc  # pragma: no cover


async def timeout_middleware(
    next_fn: NodeFn, state: AgentState, metadata: NodeMetadata
) -> dict[str, Any]:
    """Apply ``asyncio.wait_for`` and map timeouts to ``NodeTimeoutError``."""
    if metadata.timeout_s is None:
        return await next_fn(state)
    try:
        return await asyncio.wait_for(next_fn(state), timeout=metadata.timeout_s)
    except (TimeoutError, asyncio.TimeoutError) as exc:  # noqa: UP041
        raise NodeTimeoutError(
            metadata.name, _state_keys(state), exc, timeout_s=metadata.timeout_s
        ) from exc


# ── Error mapping (outermost) ────────────────────────────────────────────────


async def error_mapping_middleware(
    next_fn: NodeFn, state: AgentState, metadata: NodeMetadata
) -> dict[str, Any]:
    """Catch failures and map to ``NodeExecutionError`` or an error state_update."""
    try:
        return await next_fn(state)
    except NodeTimeoutError as exc:
        if metadata.raise_on_error:
            raise
        return _error_update(metadata.name, exc.cause, timeout=True)
    except NodeExecutionError as exc:
        if metadata.raise_on_error:
            raise
        return _error_update(metadata.name, exc.cause, timeout=False)
    except Exception as exc:
        if metadata.raise_on_error:
            raise NodeExecutionError(metadata.name, _state_keys(state), exc) from exc
        return _error_update(metadata.name, exc, timeout=False)


def _error_update(node_name: str, cause: BaseException, *, timeout: bool) -> dict[str, Any]:
    return {
        "metadata": {
            "error": {
                "node": node_name,
                "type": type(cause).__name__,
                "message": str(cause),
                "timeout": timeout,
            }
        }
    }


# ── Composition ───────────────────────────────────────────────────────────────

# Outermost → innermost. Order rationale documented in ``decorators.prismal_node``.
DEFAULT_MIDDLEWARE_STACK: list[Middleware] = [
    error_mapping_middleware,
    otel_middleware,
    logger_middleware,
    security_middleware,
    audit_middleware,
    retry_middleware,
    timeout_middleware,
]


def _wrap(mw: Middleware, next_fn: NodeFn, metadata: NodeMetadata) -> NodeFn:
    async def wrapped(state: AgentState) -> dict[str, Any]:
        return await mw(next_fn, state, metadata)

    return wrapped


def build_pipeline(
    user_fn: NodeFn,
    metadata: NodeMetadata,
    stack: list[Middleware] | None = None,
) -> NodeFn:
    """Compose the middleware ``stack`` around ``user_fn`` (stack[0] outermost)."""
    chain = stack if stack is not None else DEFAULT_MIDDLEWARE_STACK
    fn: NodeFn = user_fn
    for mw in reversed(chain):
        fn = _wrap(mw, fn, metadata)
    return fn


__all__ = [
    "DEFAULT_MIDDLEWARE_STACK",
    "Middleware",
    "audit_middleware",
    "build_pipeline",
    "error_mapping_middleware",
    "logger_middleware",
    "otel_middleware",
    "retry_middleware",
    "security_middleware",
    "timeout_middleware",
]
